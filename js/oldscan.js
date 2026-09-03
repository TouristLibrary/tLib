// Version 1.1 - 13.04.2026
// Логика страницы "Архивные сканы": генерация ссылки для скачивания по Шифру и ДопШифру.

function makeLink() {
    const code = document.getElementById('code').value.trim();
    if (!code) return;
    const suffix = document.getElementById('suffix').value.trim();
    const filename = suffix ? code + '-' + suffix + '.old.zip' : code + '.old.zip';
    const url = 'https://filedn.eu/laITJQDRIPpbOjdHpDVwUCb/TlibOldScan/'
              + encodeURIComponent(filename);
    const resultEl = document.getElementById('result');
    resultEl.innerHTML = '<a href="' + url + '" target="_blank" rel="noopener noreferrer">'
                        + filename + '</a>';
    resultEl.classList.add('visible');
}

document.addEventListener('DOMContentLoaded', function () {
    document.querySelector('form').addEventListener('submit', function (e) {
        e.preventDefault();
        makeLink();
    });
});
