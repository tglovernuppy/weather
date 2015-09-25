# -*- coding: utf-8 -*-
__author__ = 'masayuki'

import os
import sys
sys.path.append('/usr/local/lib/python2.7/site-packages')
sys.path.append(os.path.join(os.path.dirname(__file__), ".."))

import datetime

import numpy
import theano

from models.convlstm_encoder_decoder import ConvLSTMEncoderDecoder
from testbed.utils import ndarray

def unzip(params):
    return params # FIXME: need deepcopy

def get_minibatches_idx(n, minibatch_size, shuffle=False):
    """
    Used to shuffle the dataset at each iteration.
    """
    idx_list = numpy.arange(n, dtype="int32")

    if shuffle:
        numpy.random.shuffle(idx_list)

    minibatches = []
    minibatch_start = 0
    for i in range(n // minibatch_size):
        minibatches.append(idx_list[minibatch_start:
        minibatch_start + minibatch_size])
        minibatch_start += minibatch_size

    if (minibatch_start != n):
        # Make a minibatch out of what is left
        minibatches.append(idx_list[minibatch_start:])

    return zip(range(len(minibatches)), minibatches)

def moving_mnist_load_dataset(train_dataset, valid_dataset, test_dataset):
    '''
    load datasets
    :param train_dataset:
    :param valid_dataset:
    :param test_dataset:
    :return:
    '''
    def load(file):
        nda = numpy.load(file)
        input_raw_data = nda['input_raw_data']
        clips = nda['clips']
        xs = [input_raw_data[clips[0,i,0]:clips[0,i,0]+clips[0,i,1]] for i in xrange(clips.shape[1])]
        ys = [input_raw_data[clips[1,i,0]:clips[1,i,0]+clips[1,i,1]] for i in xrange(clips.shape[1])]
        return (ndarray(xs), ndarray(ys))

    # load dataset
    train = load(train_dataset)
    valid = load(valid_dataset)
    test = load(test_dataset)

    # use only one part of the dataset to avoid MemoryError: alloc failed
    # train = (train[0][:1000], train[1][:1000])
    # valid = (valid[0][:200], valid[1][:200])
    # test = (test[0][:300], test[1][:300])

    return (train, valid, test)

def exp_moving_mnist(
        train_dataset='../data/moving_mnist/out/moving-mnist-train.npz',
        valid_dataset='../data/moving_mnist/out/moving-mnist-valid.npz',
        test_dataset='../data/moving_mnist/out/moving-mnist-test.npz',
        filter_shapes=[(1,1,3,3)],
        saveto='out/states.npz',
        patience=10,  # Number of epoch to wait before early stop if no progress
        max_epochs=5000,  # The maximum number of epoch to run
        validFreq=None,  # Compute the validation error after this number of update.
        saveFreq=None,  # Save the parameters after every saveFreq updates
        batch_size=16,  # The batch size during training.
        valid_batch_size=64,  # The batch size used for validation/test set.
):
    '''
    make experiment on Moving MNIST dataset
    :param train_dataset:
    :param valid_dataset:
    :param test_dataset:
    :param filter_shapes:
    :param states_file:
    :return:
    '''

    # load dataset
    datasets = moving_mnist_load_dataset(train_dataset, valid_dataset, test_dataset)
    train_data, valid_data, test_data = datasets

    # check if the output directory exists and make directory if necessary
    outdir = os.path.dirname(saveto)
    if not os.path.exists(outdir):
        os.makedirs(outdir)

    # determine parameters
    d, h, w = train_data[0].shape[2], train_data[0].shape[3], train_data[0].shape[4]
    t_in, t_out = train_data[0].shape[1], train_data[1].shape[1]

    # build model
    numpy_rng = numpy.random.RandomState(89677)
    model = ConvLSTMEncoderDecoder(numpy_rng, t_in=t_in, d=d, w=w, h=h, t_out=t_out, filter_shapes=filter_shapes)
    f_grad_shared, f_update = model.build_finetune_function()
    f_predict = model.build_prediction_function()

    kf_train = get_minibatches_idx(len(train_data[0]), batch_size)
    kf_valid = get_minibatches_idx(len(valid_data[0]), valid_batch_size)
    kf_test = get_minibatches_idx(len(test_data[0]), valid_batch_size)

    print("{0} train examples".format((len(train_data[0]))))
    print("{0} valid examples".format(len(valid_data[0])))
    print("{0} test examples".format(len(test_data[0])))

    # bunch of configs
    dispFreq = 1
    if validFreq is None:
        validFreq = len(train_data[0]) / batch_size
    if saveFreq is None:
        saveFreq = len(train_data[0]) / batch_size

    def pred_error(data, iterator):
        """
        Just compute the error
        """
        valid_err = 0
        for _, valid_index in iterator:
            x, mask, y = model.prepare_data([data[0][t] for t in valid_index],
                                            numpy.array(data[1])[valid_index],
                                            maxlen=None)
            y_ = f_predict(x, mask)
            err = numpy.mean((y - y_)**2)
            valid_err += err
        valid_err = 1. - numpy.asarray(valid_err, dtype=theano.config.floatX) / len(data[0])

        return valid_err

    def train(learning_rate, max_epochs):
        # training phase
        history_errs = []
        best_p = None
        bad_counter = 0

        uidx = 0  # the number of update done
        estop = False  # early stop
        costs = []
        for eidx in xrange(max_epochs):
            n_samples = 0

            # Get new shuffled index for the training set.
            kf = get_minibatches_idx(len(train_data[0]), batch_size, shuffle=True)

            avg_cost = 0
            for bidx, train_index in kf:
                uidx += 1
                #use_noise.set_value(1.) # TODO: implement dropout?

                # Select the random examples for this minibatch
                y = [train_data[1][t] for t in train_index]
                x = [train_data[0][t] for t in train_index]

                # Get the data in numpy.ndarray format
                # This swap the axis!
                # Return something of shape (minibatch maxlen, n samples)
                x, mask, y = model.prepare_data(x, y)
                n_samples += x.shape[1]

                cost = f_grad_shared(x, mask, y)
                f_update(learning_rate)

                avg_cost += cost / len(kf)

                if numpy.isnan(cost) or numpy.isinf(cost):
                    print('NaN detected, cost={0}'.format(cost))
                    raise Exception

                if numpy.mod(uidx, dispFreq) == 0:
                    print('Epoch {0}, Update {1}, Cost: {2}'.format(eidx, uidx, cost))
                    pass

                if saveto and numpy.mod(uidx, saveFreq) == 0:
                    print('Saving...'),

                    if best_p is not None:
                        params = best_p
                    else:
                        params = unzip(model.params)
                    numpy.savez(saveto, history_errs=history_errs, **params)
                    # pkl.dump(model_options, open('%s.pkl' % saveto, 'wb'), -1)
                    print('Done')

                if numpy.mod(uidx, validFreq) == 0:
                    #use_noise.set_value(0.) # TODO: implement dropout?
                    train_err = pred_error(train_data, kf)
                    valid_err = pred_error(valid_data, kf_valid)
                    test_err = pred_error(test_data, kf_test)

                    history_errs.append([valid_err, test_err])

                    if (uidx == 0 or
                                valid_err <= numpy.array(history_errs).min()):
                        best_p = model.params
                        bad_counter = 0

                    print(" (validtion) Train: {0}, Valid: {1}, Test: {2}".format(train_err, valid_err, test_err))

                    if (len(history_errs) > patience and
                                valid_err >= numpy.array(history_errs)[:-patience].min()):
                        bad_counter += 1
                        if bad_counter > patience:
                            print('Early Stop!')
                            estop = True
                            break

                costs.append(avg_cost)

            print("Epoch {0}/{1}: Seen {2} samples".format(eidx+1, max_epochs, n_samples))

            if estop:
                break

        train_err = pred_error(train_data, kf_train)
        valid_err = pred_error(valid_data, kf_valid)
        test_err = pred_error(test_data, kf_test)

        return train_err, valid_err, test_err

    train_err, valid_err, test_err = train(1e-3, max_epochs)
    print("Train finished. Train: {0}, Valid: {1}, Test: {2}".format(train_err, valid_err, test_err))


if __name__ == '__main__':
    argv = sys.argv  # コマンドライン引数を格納したリストの取得
    argc = len(argv) # 引数の個数

    if argc <= 1:
        print("Usage: $ python {0} [1|2|3|4|5]".format(argv[0]))
        quit()

    exp = int(argv[1])
    if exp == 1:
        filter_shapes = [(256,1,5,5)]
    elif exp == 2:
        filter_shapes = [(128,1,5,5),(128,128,5,5)]
    elif exp == 3:
        filter_shapes = [(128,1,5,5),(64,128,5,5),(64,64,5,5)]
    elif exp == 4:
        filter_shapes = [(128,1,9,9),(128,128,9,9)]
    elif exp == 5:
        filter_shapes = [(128,1,9,9),(64,128,9,9),(64,64,9,9)]
    elif exp == 6:
        filter_shapes = [(2,1,5,5),(2,2,9,9)]
    else:
        raise NotImplementedError

    now = datetime.datetime.today()
    saveto = "out/states-{0}-{1}.npz".format(exp,now.strftime('%Y%m%d%I%M%S'))
    print('Save file: {0}'.format(saveto))

    print('begin experiment')
    exp_moving_mnist(filter_shapes=filter_shapes, saveto=saveto)
    print('finish experiment')