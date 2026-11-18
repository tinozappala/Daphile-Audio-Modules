'use strict';
(() => {
  const link = document.getElementById('daphile-rme-panel');
  if (link) {
    const url = new URL(link.getAttribute('href'), window.location.href);
    url.port = '';
    link.href = url.href;
  }
})();
