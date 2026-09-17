var FiveLeagues_MathUtils = (function() {
  function factorial(n) {
    if (n <= 1) return 1;
    var r = 1;
    for (var i = 2; i <= n; i++) r *= i;
    return r;
  }

  function logFactorial(n) {
    if (n <= 1) return 0;
    var sum = 0;
    for (var i = 2; i <= n; i++) {
      sum += Math.log(i);
    }
    return sum;
  }

  function poissonPMF(k, lambda) {
    if (lambda <= 0) return k === 0 ? 1 : 0;
    return Math.pow(lambda, k) * Math.exp(-lambda) / factorial(k);
  }

  function bivariatePoissonPMF(x, y, lambda1, lambda2, rho) {
    rho = rho || 0.1;
    var maxK = Math.min(x, y, 8);
    var sum = 0;
    for (var k = 0; k <= maxK; k++) {
      sum += Math.pow(rho, k) / factorial(k) *
             poissonPMF(x - k, lambda1) *
             poissonPMF(y - k, lambda2);
    }
    return sum;
  }

  function negativeBinomialPMF(k, mu, r) {
    mu = Math.max(0.001, mu);
    r = r || 3.0;
    if (k < 0) return 0;
    if (k === 0) {
      return Math.pow(r / (r + mu), r);
    }
    var p = r / (r + mu);
    var q = mu / (r + mu);
    var logProb = logFactorial(k + r - 1) - logFactorial(k) - logFactorial(r - 1) +
                  r * Math.log(p) + k * Math.log(q);
    return Math.exp(logProb);
  }

  return {
    factorial: factorial,
    logFactorial: logFactorial,
    poissonPMF: poissonPMF,
    bivariatePoissonPMF: bivariatePoissonPMF,
    negativeBinomialPMF: negativeBinomialPMF
  };
})();