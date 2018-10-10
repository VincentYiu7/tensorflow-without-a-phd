# encoding: UTF-8
# Copyright 2016 Google.com
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
# http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import math
import os
import tensorflow as tf

TB_DIR = './Graph'
print("Tensorflow version " + tf.__version__)
tf.set_random_seed(0)

def model_fn(features, labels, mode, params):
    tf.summary.image('image', features)

    # five layers and their number of neurons (tha last layer has 10 softmax neurons)
    L = 24
    M = 48
    N = 64
    O = 200

    # Weights initialised with small random values between -0.2 and +0.2
    weights = {
        "W1" : tf.Variable(tf.truncated_normal([6, 6, 1, L], stddev=0.1)),  # 5x5 patch, 1 input channel, K output channels
        "W2" : tf.Variable(tf.truncated_normal([5, 5, L, M], stddev=0.1)),
        "W3" : tf.Variable(tf.truncated_normal([4, 4, M, N], stddev=0.1)),
        "W4" : tf.Variable(tf.truncated_normal([7 * 7 * N, O], stddev=0.1)),
        "W5" : tf.Variable(tf.truncated_normal([O, 10], stddev=0.1))
    }
    # When using RELUs, make sure biases are initialised with small *positive* values for example 0.1 = tf.ones([K])/10
    biases = {
        "B4" : tf.Variable(tf.ones([O])/10),
        "B5" : tf.Variable(tf.ones([10])/10)
    }  

    def compatible_convolutional_noise_shape(Y):
        noiseshape = tf.shape(Y)
        noiseshape = noiseshape * tf.constant([1,0,0,1]) + tf.constant([0,1,1,0])
        return noiseshape

    # The model
    stride = 1  # output is 28x28
    Y1l = tf.nn.conv2d(features, weights["W1"], strides=[1, stride, stride, 1], padding='SAME')
    Y1bn = tf.layers.batch_normalization(Y1l, training=mode == tf.estimator.ModeKeys.TRAIN)
    Y1r = tf.nn.leaky_relu(Y1bn)
    Y1 = tf.nn.dropout(Y1r, params["pkeep"], compatible_convolutional_noise_shape(Y1r))
    stride = 2  # output is 14x14
    Y2l = tf.nn.conv2d(Y1, weights["W2"], strides=[1, stride, stride, 1], padding='SAME')
    Y2bn = tf.layers.batch_normalization(Y2l, training=mode == tf.estimator.ModeKeys.TRAIN)
    Y2r = tf.nn.leaky_relu(Y2bn)
    Y2 = tf.nn.dropout(Y2r, params["pkeep"], compatible_convolutional_noise_shape(Y2r))
    stride = 2  # output is 7x7
    Y3l = tf.nn.conv2d(Y2, weights["W3"], strides=[1, stride, stride, 1], padding='SAME')
    Y3bn = tf.layers.batch_normalization(Y3l, training=mode == tf.estimator.ModeKeys.TRAIN)
    Y2r = tf.nn.leaky_relu(Y3bn)
    Y3 = tf.nn.dropout(Y2r, params["pkeep"], compatible_convolutional_noise_shape(Y2r))
    
    # reshape the output from the third convolution for the fully connected layer
    YY = tf.reshape(Y3, shape=[-1, 7 * 7 * N])

    Y4l = tf.matmul(YY, weights["W4"]) + biases["B4"]
    Y4bn = tf.layers.batch_normalization(Y4l, training=mode == tf.estimator.ModeKeys.TRAIN)
    Y4r = tf.nn.relu(Y4bn)
    Y4 = tf.nn.dropout(Y4r, params["pkeep"])
    Ylogits = tf.matmul(Y4, weights["W5"]) + biases["B5"]
    Y = tf.nn.softmax(Ylogits)
    
    for k, w in weights.items():
        tf.summary.histogram(k, w)
    for k, b in biases.items():
        tf.summary.histogram(k, b)
        
    if mode == tf.estimator.ModeKeys.TRAIN or mode == tf.estimator.ModeKeys.EVAL:
        # cross-entropy loss function (= -sum(Y_i * log(Yi)) ), normalised for batches of 100  images
        # TensorFlow provides the softmax_cross_entropy_with_logits function to avoid numerical instability
        # problems with log(0) which is NaN
        cross_entropy = tf.nn.softmax_cross_entropy_with_logits_v2(logits=Ylogits, labels=tf.one_hot(labels, 10))
        cross_entropy = tf.reduce_mean(cross_entropy)*100
        # % of correct answers found in batch
        predictions = tf.argmax(Y,1)
        accuracy = tf.metrics.accuracy(predictions, labels)

        evalmetrics = {"accuracy/mnist": accuracy}
        if mode == tf.estimator.ModeKeys.TRAIN:
            tf.summary.scalar("accuracy/mnist", accuracy[1])
            # the learning rate is: # 0.0001 + 0.003 * (1/e)^(step/2000)), i.e. exponential decay from 0.003->0.0001
            lr = 0.0001 +  tf.train.exponential_decay(params["learning_rate"], tf.train.get_global_step(), 2000, 1/math.e)
            tf.summary.scalar("learning_rate", lr)
            optimizer = tf.train.AdamOptimizer(lr)
            # this is needed for batch normalization, but has no effect otherwise
            update_ops = tf.get_collection(tf.GraphKeys.UPDATE_OPS)
            with tf.control_dependencies(update_ops):
                train_step = optimizer.minimize(cross_entropy,
                                            global_step=tf.train.get_global_step())
        else:
            train_step = None
    else:
        cross_entropy = None
        train_step = None
        evalmetrics = None

    return tf.estimator.EstimatorSpec(
            mode=mode,
            predictions={"classid": predictions},
            loss=cross_entropy,
            train_op=train_step,
            eval_metric_ops=evalmetrics)


def make_input_fn(batch_size, mode, shuffle=False):
    train, test = tf.keras.datasets.mnist.load_data()
    if mode == tf.estimator.ModeKeys.TRAIN:
        mnist_x, mnist_y = train
    else:
        mnist_x, mnist_y = test
    def _input_fn():
        ds = tf.data.Dataset.from_tensor_slices((
            tf.cast(mnist_x, tf.float32)/256.0, 
            mnist_y))
        if mode == tf.estimator.ModeKeys.TRAIN:
            num_epochs = None # indefinitely
        else:
            num_epochs = 1 # end-of-input after this
        if shuffle or mode == tf.estimator.ModeKeys.TRAIN:
            ds = ds.shuffle(5000).repeat(num_epochs).batch(batch_size)
        else:
            ds = ds.repeat(num_epochs)
        ds = ds.prefetch(2)
        return ds
    return _input_fn


def train_and_evaluate(output_dir, hparams):
    estimator = tf.estimator.Estimator(
        model_fn = model_fn,
        params = hparams,
        config= tf.estimator.RunConfig(
            save_checkpoints_steps = 1000,
            log_step_count_steps=500
            ),
        model_dir = output_dir
    )
    train_spec = tf.estimator.TrainSpec(
        input_fn = make_input_fn(
            hparams['batch_size'],
            mode = tf.estimator.ModeKeys.TRAIN,
        ),
        max_steps = (50000//hparams["batch_size"]) * 20
    )
    eval_spec = tf.estimator.EvalSpec(
        input_fn = make_input_fn(
            hparams['batch_size'],
            mode = tf.estimator.ModeKeys.EVAL
        ),
        steps = 10000//hparams["batch_size"],
        start_delay_secs = 1,
        throttle_secs = 1
    )

    tf.estimator.train_and_evaluate(estimator, train_spec, eval_spec)


if __name__=='__main__':
    params = {
        "batch_size": 100,
        "learning_rate": 0.003,
        "pkeep": 0.75
    }
    model_name = 'mnist_4.2'
    train_and_evaluate(os.path.join(TB_DIR, model_name), params)
    