class DecisionTree {
  constructor(maxDepth = 3, minSamplesLeaf = 5) {
    this.maxDepth = maxDepth;
    this.minSamplesLeaf = minSamplesLeaf;
    this.tree = null;
  }

  fit(X, y) {
    this.tree = this._buildTree(X, y, 0);
    return this;
  }

  predict(X) {
    if (!this.tree) throw new Error('Model not fitted');
    return Array.isArray(X[0]) ? X.map(x => this._predictSingle(x, this.tree)) : this._predictSingle(X, this.tree);
  }

  _buildTree(X, y, depth) {
    if (depth >= this.maxDepth || X.length <= this.minSamplesLeaf || this._isHomogeneous(y)) {
      return {
        type: 'leaf',
        prediction: this._computeLeafValue(y)
      };
    }

    const { featureIdx, threshold, leftX, leftY, rightX, rightY } = this._findBestSplit(X, y);

    if (leftX.length === 0 || rightX.length === 0) {
      return {
        type: 'leaf',
        prediction: this._computeLeafValue(y)
      };
    }

    return {
      type: 'split',
      featureIdx,
      threshold,
      left: this._buildTree(leftX, leftY, depth + 1),
      right: this._buildTree(rightX, rightY, depth + 1)
    };
  }

  _findBestSplit(X, y) {
    let bestGain = -Infinity;
    let bestFeatureIdx = -1;
    let bestThreshold = null;

    const nFeatures = X[0].length;

    for (let i = 0; i < nFeatures; i++) {
      const values = [...new Set(X.map(x => x[i]))].sort((a, b) => a - b);

      for (let j = 0; j < values.length - 1; j++) {
        const threshold = (values[j] + values[j + 1]) / 2;
        const gain = this._computeGain(X, y, i, threshold);

        if (gain > bestGain) {
          bestGain = gain;
          bestFeatureIdx = i;
          bestThreshold = threshold;
        }
      }
    }

    if (bestFeatureIdx === -1) {
      return { featureIdx: 0, threshold: this._computeMean(X.map(x => x[0])), leftX: X, leftY: y, rightX: [], rightY: [] };
    }

    const leftX = [];
    const leftY = [];
    const rightX = [];
    const rightY = [];

    for (let i = 0; i < X.length; i++) {
      if (X[i][bestFeatureIdx] <= bestThreshold) {
        leftX.push(X[i]);
        leftY.push(y[i]);
      } else {
        rightX.push(X[i]);
        rightY.push(y[i]);
      }
    }

    return { featureIdx: bestFeatureIdx, threshold: bestThreshold, leftX, leftY, rightX, rightY };
  }

  _computeGain(X, y, featureIdx, threshold) {
    const leftY = [];
    const rightY = [];

    for (let i = 0; i < X.length; i++) {
      if (X[i][featureIdx] <= threshold) {
        leftY.push(y[i]);
      } else {
        rightY.push(y[i]);
      }
    }

    if (leftY.length === 0 || rightY.length === 0) return -Infinity;

    const parentImpurity = this._computeImpurity(y);
    const leftWeight = leftY.length / y.length;
    const rightWeight = rightY.length / y.length;
    const childImpurity = leftWeight * this._computeImpurity(leftY) + rightWeight * this._computeImpurity(rightY);

    return parentImpurity - childImpurity;
  }

  _computeImpurity(y) {
    if (typeof y[0] === 'number') {
      return this._computeVariance(y);
    } else {
      return this._computeEntropy(y);
    }
  }

  _computeVariance(y) {
    const mean = y.reduce((a, b) => a + b, 0) / y.length;
    return y.reduce((a, b) => a + Math.pow(b - mean, 2), 0) / y.length;
  }

  _computeEntropy(y) {
    const counts = {};
    y.forEach(val => { counts[val] = (counts[val] || 0) + 1; });

    let entropy = 0;
    Object.values(counts).forEach(count => {
      const p = count / y.length;
      entropy -= p * Math.log2(p);
    });

    return entropy;
  }

  _isHomogeneous(y) {
    return new Set(y).size === 1;
  }

  _computeLeafValue(y) {
    if (typeof y[0] === 'number') {
      return y.reduce((a, b) => a + b, 0) / y.length;
    } else {
      const counts = {};
      y.forEach(val => { counts[val] = (counts[val] || 0) + 1; });
      let maxCount = -1;
      let maxVal = null;
      Object.entries(counts).forEach(([val, count]) => {
        if (count > maxCount) {
          maxCount = count;
          maxVal = val;
        }
      });
      return maxVal;
    }
  }

  _predictSingle(x, node) {
    if (node.type === 'leaf') {
      return node.prediction;
    }

    if (x[node.featureIdx] <= node.threshold) {
      return this._predictSingle(x, node.left);
    } else {
      return this._predictSingle(x, node.right);
    }
  }
}

class GradientBoostingClassifier {
  constructor(nEstimators = 50, learningRate = 0.1, maxDepth = 3, minSamplesLeaf = 5) {
    this.nEstimators = nEstimators;
    this.learningRate = learningRate;
    this.maxDepth = maxDepth;
    this.minSamplesLeaf = minSamplesLeaf;
    this.trees = [];
    this.classes = [];
    this.initPrediction = null;
  }

  fit(X, y) {
    this.classes = [...new Set(y)];
    const nClasses = this.classes.length;

    if (nClasses === 2) {
      this._fitBinary(X, y);
    } else {
      this._fitMulticlass(X, y);
    }

    return this;
  }

  _fitBinary(X, y) {
    const yEncoded = y.map(val => val === this.classes[0] ? 0 : 1);
    let predictions = Array(X.length).fill(this._logitInverse(this._computeLogit(yEncoded)));

    for (let i = 0; i < this.nEstimators; i++) {
      const residuals = yEncoded.map((yi, idx) => yi - predictions[idx]);
      
      const tree = new DecisionTree(this.maxDepth, this.minSamplesLeaf);
      tree.fit(X, residuals);
      this.trees.push(tree);

      const treePredictions = tree.predict(X);
      predictions = predictions.map((p, idx) => {
        const newP = p + this.learningRate * treePredictions[idx];
        return Math.max(0.001, Math.min(0.999, newP));
      });
    }

    this.initPrediction = predictions[0];
  }

  _fitMulticlass(X, y) {
    const nClasses = this.classes.length;
    this.trees = Array(nClasses).fill(null).map(() => []);

    for (let c = 0; c < nClasses; c++) {
      const yEncoded = y.map(val => val === this.classes[c] ? 1 : 0);
      let predictions = Array(X.length).fill(0.5);

      for (let i = 0; i < this.nEstimators; i++) {
        const residuals = yEncoded.map((yi, idx) => yi - predictions[idx]);

        const tree = new DecisionTree(this.maxDepth, this.minSamplesLeaf);
        tree.fit(X, residuals);
        this.trees[c].push(tree);

        const treePredictions = tree.predict(X);
        predictions = predictions.map((p, idx) => {
          const newP = p + this.learningRate * treePredictions[idx];
          return Math.max(0.001, Math.min(0.999, newP));
        });
      }
    }
  }

  predict(X) {
    if (this.trees.length === 0) throw new Error('Model not fitted');

    const probs = this.predictProba(X);
    
    if (Array.isArray(X[0])) {
      return probs.map(p => this.classes[p.indexOf(Math.max(...p))]);
    } else {
      return this.classes[probs.indexOf(Math.max(...probs))];
    }
  }

  predictProba(X) {
    if (this.trees.length === 0) throw new Error('Model not fitted');

    const isBinary = !Array.isArray(this.trees[0]);

    if (isBinary) {
      let prob = 0.5;
      this.trees.forEach(tree => {
        prob += this.learningRate * tree.predict(X);
      });
      prob = Math.max(0.001, Math.min(0.999, prob));

      if (Array.isArray(X[0])) {
        return prob.map(p => [1 - p, p]);
      } else {
        return [1 - prob, prob];
      }
    } else {
      const nClasses = this.classes.length;

      if (Array.isArray(X[0])) {
        return X.map(x => {
          const probs = [];
          for (let c = 0; c < nClasses; c++) {
            let prob = 0.5;
            this.trees[c].forEach(tree => {
              prob += this.learningRate * tree.predict(x);
            });
            probs.push(Math.max(0.001, Math.min(0.999, prob)));
          }
          const sum = probs.reduce((a, b) => a + b, 0);
          return probs.map(p => p / sum);
        });
      } else {
        const probs = [];
        for (let c = 0; c < nClasses; c++) {
          let prob = 0.5;
          this.trees[c].forEach(tree => {
            prob += this.learningRate * tree.predict(X);
          });
          probs.push(Math.max(0.001, Math.min(0.999, prob)));
        }
        const sum = probs.reduce((a, b) => a + b, 0);
        return probs.map(p => p / sum);
      }
    }
  }

  _computeLogit(y) {
    const pos = y.filter(v => v === 1).length;
    const neg = y.length - pos;
    return Math.log(pos / neg);
  }

  _logitInverse(x) {
    return 1 / (1 + Math.exp(-x));
  }

  getFeatureImportances() {
    if (this.trees.length === 0) throw new Error('Model not fitted');
    
    const nFeatures = this.trees[0] instanceof DecisionTree ? 
      this._estimateFeatureCount(this.trees[0]) : 
      this._estimateFeatureCount(this.trees[0][0]);
    
    const importances = Array(nFeatures).fill(0);
    
    const trees = Array.isArray(this.trees[0]) ? 
      this.trees.flat() : this.trees;
    
    trees.forEach(tree => {
      const treeImportance = this._computeTreeImportance(tree, nFeatures);
      treeImportance.forEach((imp, idx) => {
        importances[idx] += imp * this.learningRate;
      });
    });
    
    const total = importances.reduce((a, b) => a + b, 0);
    return total > 0 ? importances.map(i => i / total) : importances;
  }

  _estimateFeatureCount(tree) {
    if (!tree || !tree.tree) return 0;
    const features = new Set();
    this._collectFeatures(tree.tree, features);
    return features.size || 226;
  }

  _collectFeatures(node, features) {
    if (!node) return;
    if (node.type === 'split' && node.featureIdx !== undefined) {
      features.add(node.featureIdx);
    }
    if (node.left) this._collectFeatures(node.left, features);
    if (node.right) this._collectFeatures(node.right, features);
  }

  _computeTreeImportance(tree, nFeatures) {
    const importances = Array(nFeatures).fill(0);
    if (!tree || !tree.tree) return importances;
    
    const nodeCount = this._countNodes(tree.tree);
    this._computeNodeImportance(tree.tree, importances, 1.0 / nodeCount);
    
    return importances;
  }

  _countNodes(node) {
    if (!node) return 0;
    return 1 + this._countNodes(node.left) + this._countNodes(node.right);
  }

  _computeNodeImportance(node, importances, weight) {
    if (!node) return;
    if (node.type === 'split' && node.featureIdx !== undefined && node.featureIdx < importances.length) {
      importances[node.featureIdx] += weight;
    }
    const childWeight = weight * 0.5;
    this._computeNodeImportance(node.left, importances, childWeight);
    this._computeNodeImportance(node.right, importances, childWeight);
  }
}

class PoissonRegression {
  constructor(learningRate = 0.01, maxIterations = 1000, tolerance = 1e-6) {
    this.learningRate = learningRate;
    this.maxIterations = maxIterations;
    this.tolerance = tolerance;
    this.coefficients = null;
  }

  fit(X, y) {
    const nSamples = X.length;
    const nFeatures = X[0].length;
    this.coefficients = Array(nFeatures).fill(0);

    for (let iteration = 0; iteration < this.maxIterations; iteration++) {
      let gradientSum = 0;

      for (let i = 0; i < nSamples; i++) {
        const lambda = Math.exp(X[i].reduce((sum, x, j) => sum + x * this.coefficients[j], 0));
        const residual = y[i] - lambda;

        for (let j = 0; j < nFeatures; j++) {
          const gradient = -residual * X[i][j];
          this.coefficients[j] -= this.learningRate * gradient;
          gradientSum += Math.abs(gradient);
        }
      }

      if (gradientSum < this.tolerance * nSamples) {
        break;
      }
    }

    return this;
  }

  predict(X) {
    if (!this.coefficients) throw new Error('Model not fitted');

    if (Array.isArray(X[0])) {
      return X.map(x => Math.exp(x.reduce((sum, xi, j) => sum + xi * this.coefficients[j], 0)));
    } else {
      return Math.exp(X.reduce((sum, xi, j) => sum + xi * this.coefficients[j], 0));
    }
  }

  predictProbability(X, k) {
    const lambda = this.predict(X);
    if (Array.isArray(lambda)) {
      return lambda.map(l => this._poissonPmf(l, k));
    } else {
      return this._poissonPmf(lambda, k);
    }
  }

  _poissonPmf(lambda, k) {
    return Math.exp(-lambda) * Math.pow(lambda, k) / this._factorial(k);
  }

  _factorial(k) {
    let result = 1;
    for (let i = 2; i <= k; i++) {
      result *= i;
    }
    return result;
  }
}

class EnsembleModel {
  constructor(models, weights = null) {
    this.models = models;
    this.weights = weights || Array(models.length).fill(1 / models.length);
  }

  fit(X, y) {
    this.models.forEach(model => model.fit(X, y));
    return this;
  }

  predict(X) {
    const predictions = this.models.map(model => model.predict(X));
    
    if (Array.isArray(X[0])) {
      const nSamples = X.length;
      const results = [];

      for (let i = 0; i < nSamples; i++) {
        const predCounts = {};
        this.models.forEach((model, idx) => {
          const pred = predictions[idx][i];
          predCounts[pred] = (predCounts[pred] || 0) + this.weights[idx];
        });

        let maxWeight = -1;
        let bestPred = null;
        Object.entries(predCounts).forEach(([pred, weight]) => {
          if (weight > maxWeight) {
            maxWeight = weight;
            bestPred = pred;
          }
        });
        results.push(bestPred);
      }

      return results;
    } else {
      const predCounts = {};
      this.models.forEach((model, idx) => {
        const pred = predictions[idx];
        predCounts[pred] = (predCounts[pred] || 0) + this.weights[idx];
      });

      let maxWeight = -1;
      let bestPred = null;
      Object.entries(predCounts).forEach(([pred, weight]) => {
        if (weight > maxWeight) {
          maxWeight = weight;
          bestPred = pred;
        }
      });

      return bestPred;
    }
  }

  predictProba(X) {
    const probas = this.models.map(model => model.predictProba ? model.predictProba(X) : null);
    
    if (!probas[0]) return null;

    if (Array.isArray(X[0])) {
      const nSamples = X.length;
      const nClasses = probas[0][0].length;

      return X.map((_, i) => {
        const avgProbs = Array(nClasses).fill(0);
        probas.forEach((proba, idx) => {
          if (proba) {
            proba[i].forEach((p, c) => {
              avgProbs[c] += p * this.weights[idx];
            });
          }
        });
        return avgProbs;
      });
    } else {
      const nClasses = probas[0].length;
      const avgProbs = Array(nClasses).fill(0);

      probas.forEach((proba, idx) => {
        if (proba) {
          proba.forEach((p, c) => {
            avgProbs[c] += p * this.weights[idx];
          });
        }
      });

      return avgProbs;
    }
  }

  setWeights(weights) {
    this.weights = weights;
    const sum = weights.reduce((a, b) => a + b, 0);
    this.weights = this.weights.map(w => w / sum);
  }
}

class BayesianParameterOptimizer {
  constructor(paramSpace, objectiveFunc, nIterations = 50) {
    this.paramSpace = paramSpace;
    this.objectiveFunc = objectiveFunc;
    this.nIterations = nIterations;
    this.bestParams = null;
    this.bestScore = -Infinity;
    this.history = [];
  }

  optimize() {
    for (let i = 0; i < this.nIterations; i++) {
      const params = this._sampleParams();
      const score = this.objectiveFunc(params);

      this.history.push({ params, score });

      if (score > this.bestScore) {
        this.bestScore = score;
        this.bestParams = { ...params };
      }

      if (i > 5) {
        this._updatePrior();
      }
    }

    return { bestParams: this.bestParams, bestScore: this.bestScore, history: this.history };
  }

  _sampleParams() {
    const params = {};
    Object.entries(this.paramSpace).forEach(([key, space]) => {
      if (space.type === 'int') {
        params[key] = Math.floor(Math.random() * (space.max - space.min + 1)) + space.min;
      } else if (space.type === 'float') {
        params[key] = Math.random() * (space.max - space.min) + space.min;
      } else if (space.type === 'categorical') {
        params[key] = space.values[Math.floor(Math.random() * space.values.length)];
      }
    });
    return params;
  }

  _updatePrior() {
  }
}

export {
  DecisionTree,
  GradientBoostingClassifier,
  PoissonRegression,
  EnsembleModel,
  BayesianParameterOptimizer
};
