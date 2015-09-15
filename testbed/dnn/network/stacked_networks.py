# -*- coding: utf-8 -*-
import numpy
import theano
import theano.tensor as T
from theano.tensor.shared_randomstreams import RandomStreams
from theano.gof.utils import flatten

from base import Network, tensor5
from layer import LSTM, ConvLSTM

import optimizers as O

class StackedNetwork(Network):
    '''
    Base implementation of Stacked Network
    '''
    def __init__(self,
                 numpy_rng,
                 theano_rng=None,
                 input=None,
                 mask=None,
                 output=None
    ):
        self.x = input
        self.mask = mask
        self.y = output
        self.layers = []

        assert input is not None
        assert output is not None

        super(StackedNetwork, self).__init__(numpy_rng, theano_rng)

    def setup(self):
        '''
        Construct the stacked network
        :return:
        '''
        raise NotImplementedError

    @property
    def params(self):
        return [[layer.params] for layer in self.layers]

    @params.setter
    def params(self, param_list):
        for layer, params in zip(self.layers, param_list):
            layer.params = params

    def build_finetune_function(self, cost, optimizer=O.adadelta):
        learning_rate = T.scalar('lr', dtype=theano.config.floatX)

        params = flatten(self.params)
        grads = T.grad(cost, params)

        f_validate = theano.function([self.x, self.mask, self.y], cost)

        f_grad_shared, f_update = optimizer(learning_rate, params, grads,
                                            self.x, self.mask, self.y, cost)

        return (f_grad_shared, f_update, f_validate)

    def build_prediction_function(self):
        return theano.function(
            [self.x, self.mask],
            outputs=self.output
        )


class StackedLSTM(StackedNetwork):
    '''
    An implementation of Stacked LSTM
    '''
    def __init__(self,
                 numpy_rng,
                 theano_rng=None,
                 input=None,
                 mask=None,
                 output=None,
                 n_ins=784,
                 hidden_layers_sizes=[500, 500],
    ):
        self.n_ins = n_ins
        self.hidden_layers_sizes = hidden_layers_sizes
        self.n_layers = len(hidden_layers_sizes)

        # Allocate symbolic variables for the data
        if input is None:
            # the input minibatch data is of shape (n_samples, n_ins)
            input = T.tensor3('x', dtype=theano.config.floatX)
        if mask is None:
            # the input minibatch mask is of shape (n_samples, n_ins)
            mask = T.matrix('mask', dtype=theano.config.floatX) # FIXME: not used
        if output is None:
            # the output minibatch data is of shape (n_samples, n_ins)
            output = T.matrix('y', dtype=theano.config.floatX)

        super(StackedLSTM, self).__init__(numpy_rng, theano_rng, input, mask, output)

    def setup(self):
        # construct LSTM layers
        self.layers = []
        for i, n_hidden in enumerate(self.hidden_layers_sizes):
            # determine input size
            if i == 0:
                input_size = self.n_ins
            else:
                input_size = self.hidden_layers_sizes[i - 1]

            # build an LSTM layer
            layer = LSTM(n_in=input_size,
                         n_out=self.hidden_layers_sizes[i],
                         activation=T.tanh,
                         prefix="LSTM{}".format(i),
                         nrng=self.numpy_rng,
                         trng=self.theano_rng)
            self.layers.append(layer)

        self.setup_scan()

    def setup_scan(self):
        n_timesteps = self.x.shape[0]
        n_samples = self.x.shape[1]

        outputs_info = []
        for layer in self.layers:
            outputs_info += layer.outputs_info(n_samples)

        # feed forward calculation
        def step(m, x, *prev_states):
            x_ = x
            new_states = []
            for i, layer in enumerate(self.layers):
                c_, h_ = prev_states[2*i], prev_states[2*i+1]
                layer_out = layer.step(m, x_, c_, h_)
                _, x_ = layer_out # c, h
                new_states += layer_out
            return new_states

        rval, updates = theano.scan(
            step,
            sequences=[self.mask, self.x],
            n_steps=n_timesteps,
            outputs_info=outputs_info,
            name="StackedLSTM"
        )
        self.rval = rval

        # rval には n_timestamps 分の step() の戻り値 new_states が入っている
        #assert(len(rval) == 3*self.n_layers)
        # * rval[0]: n_timesteps x n_samples x hidden_layer_sizes[0] の LSTM0_h
        # * rval[1]: n_timesteps x n_samples x hidden_layer_sizes[0] の LSTM0_c
        # * rval[2]: n_timesteps x n_samples x hidden_layer_sizes[1] の LSTM0_h
        # ...

        self.finetune_cost = (self.output - self.y).norm(L=2) / n_timesteps

    @property
    def output(self):
        '''
        :return: the output of the last layer at the last time period
        '''
        return self.rval[-1][-1]

    @property
    def outputs(self):
        '''
        :return: the outputs of the last layer from time period 0 to T
        '''
        return self.rval[-1]


class StackedLSTMEncoder(StackedLSTM):
    '''
    An implementation of Stacked LSTM Encoder
    '''

    def __init__(self,
                 numpy_rng,
                 theano_rng=None,
                 input=None,
                 mask=None,
                 output=None,
                 n_ins=784,
                 hidden_layers_sizes=[500, 500]):

        # in order to construct Encoder-Decoder network properly,
        # we need the output of the same size as the input
        assert n_ins == hidden_layers_sizes[0] and n_ins == hidden_layers_sizes[-1]

        super(StackedLSTMEncoder, self).__init__(numpy_rng, theano_rng, input, mask, output, n_ins, hidden_layers_sizes)

    @property
    def last_states(self):
        return [
            [
                self.rval[2*i][-1],     # LSTM[i].c[T]
                self.rval[2*i+1][-1],   # LSTM[i].h[T]
            ] for i in xrange(self.n_layers)
        ]


class StackedLSTMDecoder(StackedLSTM):
    '''
    An implementation of Stacked LSTM Decoder
    '''

    def __init__(self,
                 numpy_rng,
                 theano_rng=None,
                 input=None,
                 mask=None,
                 output=None,
                 encoder=None,
                 n_timesteps=None
    ):
        assert encoder is not None
        assert n_timesteps is not None

        n_ins = encoder.n_ins
        hidden_layers_sizes = [s for s in reversed(encoder.hidden_layers_sizes)]
        initial_hidden_states = [s for s in reversed(encoder.last_states)]

        self.initial_hidden_states = initial_hidden_states
        self.n_timesteps = n_timesteps

        super(StackedLSTMDecoder, self).__init__(numpy_rng, theano_rng, input, mask, output, n_ins, hidden_layers_sizes)

    def setup_scan(self):
        n_timesteps = self.n_timesteps

        # set initial states of layers: flatten the given state list
        outputs_info = [self.x[-1]]
        outputs_info += flatten(self.initial_hidden_states)

        # feed forward calculation
        def step(y, *prev_states):
            y_ = y
            new_states = []
            for i, layer in enumerate(self.layers):
                c_, h_ = prev_states[2*i], prev_states[2*i+1]
                layer_out = layer.step(1., y_, c_, h_)
                _, y_ = layer_out # c, h
                new_states += layer_out
            return [y_] + new_states

        rval, updates = theano.scan(
            step,
            n_steps=n_timesteps,
            outputs_info=outputs_info, # changed: dim_proj --> self.n_ins --> hidden_layer_sizes[i]
            name="StackedLSTM_Decoder"
        )
        self.rval = rval

        # rval には n_timestamps 分の step() の戻り値 new_states が入っている
        #assert(len(rval) == 3*self.n_layers)
        # * rval[0]: n_timesteps x n_samples x hidden_layer_sizes[0] の LSTM0_c
        # * rval[1]: n_timesteps x n_samples x hidden_layer_sizes[0] の LSTM0_h
        # * rval[2]: n_timesteps x n_samples x hidden_layer_sizes[1] の LSTM0_c
        # ...

        self.finetune_cost = (self.output - self.y).norm(L=2) / n_timesteps


class StackedConvLSTM(StackedNetwork):
    '''
    an implementation of Stacked ConvLSTM
    see: https://github.com/JonathanRaiman/theano_lstm/blob/master/theano_lstm/__init__.py
    '''
    def __init__(
            self,
            numpy_rng,
            theano_rng=None,
            input=None,
            mask=None,
            output=None,
            input_shape=(1,28,28),
            filter_shapes=[(1,1,3,3)]
    ):
        '''
        Initialize StackedConvLSTM
        :param numpy_rng:
        :param theano_rng:

        :type input_shape: tuple or list of length 3
        :param input_shape: (num input feature maps, image height, image width)

        :type filter_shapes: list of "tuple or list of length 4"
        :param filter_shapes: [(number of filters, num input feature maps, filter height, filter width)]

        :type initial_hidden_states: list of initial hidden states
        :param initial_hidden_states: list of initial hidden states
        :return:
        '''
        self.input_shape = input_shape
        self.filter_shapes = filter_shapes
        self.output_shape = (input_shape[0], filter_shapes[-1][0], input_shape[1], input_shape[2])
        self.n_outs = numpy.prod(input_shape[1:])
        self.conv_lstm_layers = []
        self.n_layers = len(filter_shapes)

        assert self.n_layers > 0

        # Allocate symbolic variables for the data
        if input is None:
            # the input minibatch data is of shape (n_timestep, n_samples, n_feature_maps, height, width)
            input = tensor5('x', dtype=theano.config.floatX)
        if mask is None:
            # the input minibatch mask is of shape (n_timestep, n_samples, n_feature_maps)
            mask = T.tensor3('mask', dtype=theano.config.floatX) # FIXME: not used
        if output is None:
            # the output minibatch data is of shape (n_samples, n_feature_maps, height, width)
            output = T.tensor4('y', dtype=theano.config.floatX)

        super(StackedConvLSTM, self).__init__(numpy_rng, theano_rng, input, mask, output)

    def setup(self):
        # construct LSTM layers
        for i, n_hidden in enumerate(self.filter_shapes):
            # determine input size
            if i == 0:
                s_in = self.input_shape
            else:
                s_in = self.layers[-1].output_shape

            # build an LSTM layer
            layer = ConvLSTM(input_shape=s_in,
                             filter_shape=self.filter_shapes[i],
                             activation=T.tanh,
                             prefix="ConvLSTM{}".format(i),
                             nrng=self.numpy_rng,
                             trng=self.theano_rng)
            self.layers.append(layer)

        # setup feed forward formulation
        self.setup_scan()

    def setup_scan(self):
        n_timesteps = self.x.shape[0]
        n_samples = self.x.shape[1]

        # set initial states of layers
        outputs_info = []
        for layer in self.layers:
            outputs_info += layer.outputs_info(n_samples)

        # feed forward calculation
        def step(m, x, *prev_states):
            x_ = x
            new_states = []
            for i, layer in enumerate(self.layers):
                c_, h_ = prev_states[2*i], prev_states[2*i+1]
                layer_out = layer.step(m, x_, c_, h_)
                _, x_ = layer_out # hidden, c
                new_states += layer_out
            return new_states

        rval, updates = theano.scan(
            step,
            sequences=[self.mask, self.x],
            n_steps=n_timesteps,
            outputs_info=outputs_info, # changed: dim_proj --> self.n_ins --> hidden_layer_sizes[i]
            name="StackedConvLSTM"
        )
        self.rval = rval

        # rval には n_timestamps 分の step() の戻り値 new_states が入っている
        #assert(len(rval) == 3*self.n_layers)
        # * rval[0]: n_timesteps x n_samples x hidden_layer_sizes[0] の LSTM0_c
        # * rval[1]: n_timesteps x n_samples x hidden_layer_sizes[0] の LSTM0_h
        # * rval[2]: n_timesteps x n_samples x hidden_layer_sizes[1] の LSTM0_c
        # ...

        self.finetune_cost = (self.output - self.y).norm(L=2) / n_timesteps

    @property
    def output(self):
        '''
        :return: the output of the last layer at the last time period
        '''
        return self.rval[-1][-1]

    @property
    def outputs(self):
        '''
        :return: the outputs of the last layer from time period 0 to T
        '''
        return self.rval[-1]


class StackedConvLSTMEncoder(StackedConvLSTM):
    '''
    An implementation of Stacked ConvLSTM Encoder
    '''
    @property
    def last_states(self):
        return [
            [
                self.rval[2*i][-1],     # ConvLSTM[i].c[T]
                self.rval[2*i+1][-1],   # ConvLSTM[i].h[T]
            ] for i in xrange(self.n_layers)
        ]


class StackedConvLSTMDecoder(StackedConvLSTM):
    '''
    An implementation of Stacked ConvLSTM Decoder
    '''
    def __init__(self,
                 numpy_rng,
                 theano_rng=None,
                 input=None,
                 mask=None,
                 output=None,
                 encoder=None,
                 n_timesteps=None,
    ):
        assert encoder is not None
        input_shape = encoder.input_shape
        filter_shapes = [s for s in reversed(encoder.filter_shapes)]
        initial_hidden_states = [s for s in reversed(encoder.last_states)]

        self.encoder = encoder
        self.n_timesteps = n_timesteps
        self.initial_hidden_states = initial_hidden_states

        assert n_timesteps is not None

        super(StackedConvLSTMDecoder, self).__init__(numpy_rng, theano_rng, input, mask, output, input_shape, filter_shapes)

    def setup_scan(self):
        n_timesteps = self.n_timesteps

        # set initial states of layers: flatten the given state list
        outputs_info = [self.x[-1]]
        outputs_info += flatten(self.initial_hidden_states)

        # feed forward calculation
        def step(y, *prev_states):
            y_ = y
            new_states = []
            for i, layer in enumerate(self.layers):
                c_, h_ = prev_states[2*i], prev_states[2*i+1]
                layer_out = layer.step(1., y_, c_, h_)
                _, y_ = layer_out # c, h
                new_states += layer_out
            return [y_] + new_states

        rval, updates = theano.scan(
            step,
            n_steps=n_timesteps,
            outputs_info=outputs_info, # changed: dim_proj --> self.n_ins --> hidden_layer_sizes[i]
            name="StackedConvLSTM_Decoder"
        )
        self.rval = rval

        # rval には n_timestamps 分の step() の戻り値 new_states が入っている
        #assert(len(rval) == 3*self.n_layers)
        # * rval[0]: n_timesteps x n_samples x hidden_layer_sizes[0] の LSTM0_c
        # * rval[1]: n_timesteps x n_samples x hidden_layer_sizes[0] の LSTM0_h
        # * rval[2]: n_timesteps x n_samples x hidden_layer_sizes[1] の LSTM0_c
        # ...

        self.finetune_cost = (self.output - self.y).norm(L=2) / n_timesteps
