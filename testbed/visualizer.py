import time
import numpy
import pylab as plt

from generator import SinGenerator, RadarGenerator

VIS_DEPTH = 0

def fixed_append(list, item, maxlen):
    list.append(item)
    while maxlen < len(list):
        list.pop(0)

class ObservationLocation:
    def __init__(self, vis, xy, fignum, onclose):
        def handle_close(event):
            onclose(event, self)

        self.vis = vis
        self.xy = xy
        self.fig = plt.figure(fignum)
        plt.clf()
        self.fig.canvas.mpl_connect('close_event', handle_close)
        self.ax = plt.subplot(111)

        x, y = self.xy
        data_y = [ data[VIS_DEPTH,y,x] for data in self.vis.data_y ]
        data_y_pred = [ data[VIS_DEPTH,y,x] for data in self.vis.data_y_pred ]
        self.plot_y = self.ax.plot(self.vis.data_x, data_y, 'b.-')
        self.plot_y_pred = self.ax.plot(self.vis.data_x, data_y_pred, 'r.-')

        plt.show(block=False)

    def update(self):
        x, y = self.xy
        data_y = [ data[VIS_DEPTH,y,x] for data in self.vis.data_y ]
        data_y_pred = [ data[VIS_DEPTH,y,x] for data in self.vis.data_y_pred ]
        self.plot_y[0].set_data(self.vis.data_x, data_y)
        self.plot_y_pred[0].set_data(self.vis.data_x, data_y_pred)
        self.ax.set_xlim(self.vis.data_x[0], self.vis.data_x[-1])
        self.ax.set_ylim(min(numpy.min(data_y), numpy.min(data_y_pred)), max(numpy.max(data_y), numpy.max(data_y_pred)))
        self.ax.autoscale_view(scalex=False,scaley=True)
        self.fig.canvas.draw()

class LearningCurve:
    def __init__(self, vis, fignum, clim):
        self.vis = vis
        self.clim = clim
        self.fig = plt.figure(fignum)
        plt.clf()
        self.ax = plt.subplot(111)

        self.data_iterations = []
        self.data_costs = []

        self.plot_lc_train = self.ax.plot(self.vis.data_x, [], 'b.-')
        self.plot_lc_valid = self.ax.plot(self.vis.data_x, [], 'r.-')
        self.plot_lc_test  = self.ax.plot(self.vis.data_x, [], 'g.-')
        plt.show(block=False)

        self.last_itr = -1

    def append(self, train_cost, valid_cost=None, test_cost=None):
        itr = self.last_itr + 1

        fixed_append(self.data_iterations, itr, self.clim)
        fixed_append(self.data_costs, (train_cost, valid_cost, test_cost), self.clim)

        self.last_itr = itr
        self.update()

    def update(self):
        data_train_cost = [ costs[0] for costs in self.data_costs ]
        data_valid_cost = [ costs[1] for costs in self.data_costs ]
        data_test_cost = [ costs[2] for costs in self.data_costs ]

        ymin = min(numpy.min(data_train_cost), numpy.min(data_valid_cost), numpy.min(data_test_cost))
        ymax = max(numpy.max(data_train_cost), numpy.max(data_valid_cost), numpy.max(data_test_cost))

        self.plot_lc_train[0].set_data(self.data_iterations, data_train_cost)
        self.plot_lc_valid[0].set_data(self.data_iterations, data_valid_cost)
        self.plot_lc_test[0].set_data(self.data_iterations, data_test_cost)
        self.ax.set_xlim(self.data_iterations[0], self.data_iterations[-1])
        self.ax.set_ylim(
            ymin if ymin is not None else 0,
            ymax if ymax is not None else 1
        )
        self.ax.autoscale_view(scalex=False,scaley=True)
        self.fig.canvas.draw()

class Visualizer:
    def __init__(self, w=10, h=10, xlim=30, clim=100):
        # data
        self.data_x = []
        self.data_y = []
        self.data_y_pred = []

        # y
        self.fig_y = plt.figure(1)
        plt.clf()
        self.fig_y.canvas.mpl_connect('button_press_event', self.onclick)
        self.im_y = plt.imshow(numpy.zeros((w,h)), cmap=plt.cm.jet, vmin=0, vmax=1)
        self.colorbar_y = plt.colorbar()
        plt.show(block=False)

        # y_pred
        self.fig_y_pred = plt.figure(2)
        plt.clf()
        self.fig_y_pred.canvas.mpl_connect('button_press_event', self.onclick)
        self.im_y_pred = plt.imshow(numpy.zeros((w,h)), cmap=plt.cm.jet, vmin=0, vmax=1)
        self.colorbar_y_pred = plt.colorbar()
        plt.show(block=False)

        # learning curve
        self.lc = LearningCurve(self, 3, clim)

        # observations
        self.observation_locations = []
        self.next_fignum = 4

        self.last_x = -1
        self.xlim=xlim

        self.ymin=0
        self.ymax=0

    def append(self, y, y_pred):
        assert isinstance(y, numpy.ndarray)
        assert isinstance(y_pred, numpy.ndarray)

        x = self.last_x + 1

        fixed_append(self.data_x, x, self.xlim)
        fixed_append(self.data_y, y, self.xlim)
        fixed_append(self.data_y_pred, y_pred, self.xlim)

        self.last_x = x
        self.update()

    def append_lc(self, train_cost, valid_cost=None, test_cost=None):
        self.lc.append(train_cost, valid_cost, test_cost)

    def update(self):
        # y
        self.im_y.set_data(self.data_y[-1][VIS_DEPTH])
        self.fig_y.canvas.draw()

        # y_pred
        self.im_y_pred.set_data(self.data_y_pred[-1][VIS_DEPTH])
        self.fig_y_pred.canvas.draw()

        # timeseries
        for ol in self.observation_locations:
            ol.update()

    def addObservationLocation(self, xy):
        def handle_close(event, ol):
            self.observation_locations.remove(ol)

        ol = ObservationLocation(self, xy, self.next_fignum, handle_close)
        self.observation_locations.append(ol)
        self.next_fignum += 1

    def onclick(self, event):
        xy = (int(event.xdata), int(event.ydata))
        self.addObservationLocation(xy)


if __name__ == '__main__':
    w = 28
    h = 28
    delay = 0.1
    gen = RadarGenerator('../data/radar', w=w, h=h, left=0, top=80)
    vis = Visualizer(w=w, h=h)

    time.sleep(10)
    for i,y in enumerate(gen):
        print("{}: max={}".format(i,numpy.max(y)))
        vis.append(y, y)
        time.sleep(delay)