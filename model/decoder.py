import numpy as np
import tensorflow as tf
from tensorflow.python.util import nest
import tensorflow.contrib.layers as layers
from tensorflow.contrib.rnn import GRUCell, LSTMCell


from components.dynamic_decode import dynamic_decode
from components.attention_mechanism import AttentionMechanism
from components.attention_cell import AttentionCell
from components.greedy_decoder_cell import GreedyDecoderCell
from components.beam_search_decoder_cell import BeamSearchDecoderCell
from components.beam_search_optimization import BSOCell, get_inputs, bso_cross_entropy
from components.dynamic_rnn import dynamic_rnn


class Decoder(object):
    """Implements this paper https://arxiv.org/pdf/1609.04938.pdf"""

    def __init__(self, config, n_tok, id_end):
        self._config = config
        self._n_tok = n_tok
        self._id_end = id_end
        self._tiles = 1 if config.decoding == "greedy" else config.beam_size


    def __call__(self, training, img, formula, dropout):
        """Decodes an image into a sequence of token

        Args:
            training: (tf.placeholder) bool
            img: encoded image (tf.Tensor) shape = (N, H, W, C)
            formula: (tf.placeholder), shape = (N, T)

        Returns:
            pred_train: (tf.Tensor), shape = (?, ?, vocab_size) logits of each class
            pret_test: (structure)
                - pred.test.logits, same as pred_train
                - pred.test.ids, shape = (?, config.max_length_formula)

        """
        dim_embeddings = self._config.attn_cell_config.get("dim_embeddings")
        E = tf.get_variable("E", initializer=embedding_initializer(),
                shape=[self._n_tok, dim_embeddings], dtype=tf.float32)

        start_token = tf.get_variable("start_token", dtype=tf.float32,
                shape=[dim_embeddings], initializer=embedding_initializer())

        batch_size = tf.shape(img)[0]
        decoder_output = {}

        # training
        with tf.variable_scope("attn_cell", reuse=False):
            embeddings = get_embeddings(formula, E, dim_embeddings,
                    start_token, batch_size)
            attn_meca = AttentionMechanism(img,
                    self._config.attn_cell_config["dim_e"])
            recu_cell = LSTMCell(self._config.attn_cell_config["num_units"])
            attn_cell = AttentionCell(recu_cell, attn_meca, dropout,
                    self._config.attn_cell_config, self._n_tok)

            train_outputs, _ = tf.nn.dynamic_rnn(attn_cell, embeddings[:, :-1, :],
                    initial_state=attn_cell.initial_state())
            decoder_output["train"] = train_outputs

        # decoding
        with tf.variable_scope("attn_cell", reuse=True):
            attn_meca = AttentionMechanism(img=img,
                    dim_e=self._config.attn_cell_config["dim_e"],
                    tiles=self._tiles)
            recu_cell = LSTMCell(self._config.attn_cell_config["num_units"],
                    reuse=True)
            attn_cell = AttentionCell(recu_cell, attn_meca, dropout,
                    self._config.attn_cell_config, self._n_tok)
            if self._config.decoding == "greedy":
                decoder_cell = GreedyDecoderCell(E, attn_cell, batch_size,
                        start_token, id_end)
            elif self._config.decoding == "beam_search":
                decoder_cell = BeamSearchDecoderCell(E, attn_cell, batch_size,
                        start_token, self._id_end, self._config.beam_size,
                        self._config.div_gamma, self._config.div_prob)
                if self._config.beam_search_optimization:
                    bso_cell = BSOCell(decoder_cell, bso_cross_entropy)
                    bso_inputs = get_inputs(formula, embeddings[:, 1:, :])
                    bso_outputs, _ = dynamic_rnn(bso_cell, bso_inputs,
                            initial_state=bso_cell.initial_state())
                    decoder_output["bso"] = bso_outputs

            test_outputs, _ = dynamic_decode(decoder_cell,
                    self._config.max_length_formula+1)
            decoder_output["pred"] = test_outputs

        return decoder_output


def get_embeddings(formula, E, dim, start_token, batch_size):
    """Returns the embedding of the n-1 first elements in the formula concat
    with the start token

    Args:
        formula: (tf.placeholder) tf.uint32
        E: tf.Variable (matrix)
        dim: (int) dimension of embeddings
        start_token: tf.Variable
        batch_size: tf variable extracted from placeholder

    Returns:
        embeddings_train: tensor

    """
    formula_ = tf.nn.embedding_lookup(E, formula)
    start_token_ = tf.reshape(start_token, [1, 1, dim])
    start_tokens = tf.tile(start_token_, multiples=[batch_size, 1, 1])
    embeddings = tf.concat([start_tokens, formula_[:, :, :]], axis=1)

    return embeddings


def embedding_initializer():
    """Returns initializer for embeddings"""
    def _initializer(shape, dtype, partition_info=None):
        E = tf.random_uniform(shape, minval=-1.0, maxval=1.0, dtype=dtype)
        E = tf.nn.l2_normalize(E, -1)
        return E

    return _initializer
