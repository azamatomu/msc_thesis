# april 14

import numpy as np
import matplotlib.pyplot as plt

# benchmark, epochs 21
train_loss = [4.924241805732866, 3.787332638338427, 3.5949433851560255, 3.4488125993516645, 3.353810868488463, 3.309382275077825, 3.246542358843406, 3.2060131397377574, 3.173702278508594, 3.146949465338697, 3.1013644622968393, 3.085034665381768, 2.8654890120333967, 2.8114297585993984, 2.7894162017304014, 2.7739011780982743, 2.7617557635273506, 2.7294756938985834, 2.724551014800276, 2.719700707116275]
val_loss = [3.7970716282827537, 3.441996045563937, 3.33447474772711, 3.2394945879859036, 3.1484067986204463, 3.124951342659886, 3.0923499935121366, 3.0433151308237605, 3.032860350216367, 3.0299625939629533, 2.9703344080510288, 2.9754150515557645, 2.7988583439825656, 2.782351609774251, 2.7666134868629677, 2.7563395016150873, 2.758028441822905, 2.738033477334492, 2.738874308039295, 2.7373907958529125]
x = np.asarray([i+1 for i in range(len(train_loss))])
plt.plot(x, train_loss)
plt.plot(x, val_loss)
plt.xticks(np.arange(x.min(), x.max(), 1))
plt.legend(['train', 'val'])
plt.xlabel('epoch')
plt.ylabel('loss')
plt.grid()
plt.savefig('train_test_plots/train_val_loss.png')
plt.close()


test_length = [35.77020484171323, 50.23350093109868, 58.2454748603352, 60.05409683426448, 60.64424581005583, 63.87078212290503, 64.92378026070762, 68.8520484171323, 72.5297765363129, 90.22694599627562]
x = np.asarray([i+1 for i in range(len(test_length))])
plt.plot(x, test_length)
plt.xticks(x, ['len' + str(i+1) for i in range(len(test_length))])
plt.ylim(0, max(test_length)+5)
plt.xlabel('length code')
plt.ylabel('average summary length')
plt.grid()
plt.savefig('train_test_plots/test_length_control.png')
plt.close()

# april 28

# benchmark + sentiment, epoch 28
test_sentiment = [-0.05752388640595903, -0.19361005959031663, -0.13610405959031666]
ind = np.arange(len(test_sentiment))
plt.bar(ind, test_sentiment)
plt.xticks(ind, ('POS', 'NEG', 'NEU'))
# plt.xticklabels()
plt.xlabel('sentiment code')
plt.ylabel('average compound sentiment')
plt.grid()
plt.savefig('train_test_plots/test_sentiment_control.png')
plt.close()

