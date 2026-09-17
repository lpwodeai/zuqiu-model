var FiveLeagues_SOCIAL = (function() {
  function generateShareCard(teamA, teamB, prediction) {
    var winProb = prediction.winA * 100;
    var drawProb = prediction.draw * 100;
    var loseProb = prediction.winB * 100;
    
    var bestOutcome = winProb >= drawProb && winProb >= loseProb ? teamA + '胜' :
                      (drawProb >= loseProb ? '平局' : teamB + '胜');
    
    var card = {
      title: teamA + ' vs ' + teamB,
      description: '五大联赛预测：' + bestOutcome + '\n\n' +
                   teamA + ': ' + winProb.toFixed(1) + '%\n' +
                   '平局: ' + drawProb.toFixed(1) + '%\n' +
                   teamB + ': ' + loseProb.toFixed(1) + '%',
      image: generateShareImage(teamA, teamB, prediction),
      url: window.location.href
    };
    
    return card;
  }

  function generateShareImage(teamA, teamB, prediction) {
    var canvas = document.createElement('canvas');
    canvas.width = 600;
    canvas.height = 315;
    var ctx = canvas.getContext('2d');
    
    ctx.fillStyle = '#1a1a2e';
    ctx.fillRect(0, 0, 600, 315);
    
    ctx.fillStyle = '#ffffff';
    ctx.font = 'bold 24px Arial';
    ctx.textAlign = 'center';
    ctx.fillText(teamA + ' vs ' + teamB, 300, 60);
    
    ctx.font = '16px Arial';
    ctx.fillStyle = '#a0a0a0';
    ctx.fillText('Five Leagues Prediction', 300, 90);
    
    var colors = ['#2ecc71', '#f39c12', '#e74c3c'];
    var labels = [teamA, '平局', teamB];
    var probs = [prediction.winA, prediction.draw, prediction.winB];
    
    var startX = 50;
    var barWidth = 150;
    var barHeight = 40;
    
    probs.forEach(function(prob, i) {
      var x = startX + i * 160;
      var width = prob * 150;
      
      ctx.fillStyle = colors[i];
      ctx.fillRect(x, 130 + i * 60, width, barHeight);
      
      ctx.fillStyle = '#ffffff';
      ctx.font = 'bold 14px Arial';
      ctx.textAlign = 'left';
      ctx.fillText(labels[i], x, 130 + i * 60 + 28);
      
      ctx.fillStyle = '#ffffff';
      ctx.font = 'bold 14px Arial';
      ctx.textAlign = 'right';
      ctx.fillText((prob * 100).toFixed(0) + '%', x + width - 5, 130 + i * 60 + 28);
    });
    
    return canvas.toDataURL('image/png');
  }

  function shareToWechat(card) {
    if (navigator.share) {
      navigator.share({
        title: card.title,
        text: card.description,
        url: card.url
      }).catch(function(err) {
        console.log('Share failed:', err);
      });
    } else {
      copyToClipboard(card.description + '\n\n' + card.url);
      showToast('预测结果已复制到剪贴板，请在微信中粘贴分享');
    }
  }

  function shareToWeibo(card) {
    var url = 'https://service.weibo.com/share/share.php?' +
              'title=' + encodeURIComponent(card.description) +
              '&url=' + encodeURIComponent(card.url);
    window.open(url, '_blank', 'width=600,height=400');
  }

  function shareToTwitter(card) {
    var url = 'https://twitter.com/intent/tweet?' +
              'text=' + encodeURIComponent(card.description) +
              '&url=' + encodeURIComponent(card.url);
    window.open(url, '_blank', 'width=600,height=400');
  }

  function shareToFacebook(card) {
    var url = 'https://www.facebook.com/sharer/sharer.php?' +
              'u=' + encodeURIComponent(card.url);
    window.open(url, '_blank', 'width=600,height=400');
  }

  function copyToClipboard(text) {
    navigator.clipboard.writeText(text).then(function() {
      showToast('已复制到剪贴板');
    }).catch(function(err) {
      var textarea = document.createElement('textarea');
      textarea.value = text;
      document.body.appendChild(textarea);
      textarea.select();
      document.execCommand('copy');
      document.body.removeChild(textarea);
      showToast('已复制到剪贴板');
    });
  }

  function showToast(message) {
    var toast = document.createElement('div');
    toast.style.cssText = 'position:fixed;top:50%;left:50%;transform:translate(-50%,-50%);background:rgba(0,0,0,0.8);color:white;padding:15px 30px;border-radius:8px;z-index:10000;font-size:14px;';
    toast.textContent = message;
    document.body.appendChild(toast);
    setTimeout(function() {
      document.body.removeChild(toast);
    }, 2000);
  }

  function getShareOptions() {
    return [
      { id: 'wechat', name: '微信', icon: '💬', action: shareToWechat },
      { id: 'weibo', name: '微博', icon: '📢', action: shareToWeibo },
      { id: 'twitter', name: 'Twitter', icon: '🐦', action: shareToTwitter },
      { id: 'facebook', name: 'Facebook', icon: '📘', action: shareToFacebook },
      { id: 'copy', name: '复制链接', icon: '🔗', action: function(card) { copyToClipboard(card.url); } }
    ];
  }

  return {
    generateShareCard: generateShareCard,
    shareToWechat: shareToWechat,
    shareToWeibo: shareToWeibo,
    shareToTwitter: shareToTwitter,
    shareToFacebook: shareToFacebook,
    copyToClipboard: copyToClipboard,
    getShareOptions: getShareOptions
  };
})();