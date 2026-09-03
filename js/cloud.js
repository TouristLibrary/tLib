// Version 2.0 - 29.07.2026
// Логика страницы "Отчеты в облаке": проверяет наличие отчёта в БД,
// показывает ссылку на json-карточку и (если есть файл) на zip/pdf;
// при отсутствии записи выводит "Отчет отсутствует".

let _cfg = null;

async function getConfig() {
    if (_cfg) return _cfg;
    const resp = await fetch('/api/config');
    const json = await resp.json();
    if (!json.success) throw new Error('Ошибка конфигурации');
    _cfg = json.data;
    return _cfg;
}

async function makeLinks() {
    const code = document.getElementById('code').value.trim();
    if (!code) return;
    const suffix = document.getElementById('suffix').value.trim();
    const resultEl = document.getElementById('result');
    resultEl.innerHTML = '';
    resultEl.classList.remove('visible');

    try {
        const cfg = await getConfig();
        const noDop = cfg.specialValues.noDopShifr;
        const baseUrl = cfg.paths.pcloudData.replace(/\/?$/, '/');

        const fd = new FormData();
        fd.append('Шифр', code);
        fd.append('ДопШифр', suffix || noDop);

        const resp = await fetch('/api/search', { method: 'POST', body: fd });
        const data = await resp.json();

        if (!data.success || !data.data || data.data.length === 0) {
            resultEl.textContent = 'Отчет отсутствует';
            resultEl.classList.add('visible');
            return;
        }

        const row = data.data[0];
        const shifr = String(row['Шифр']).padStart(5, '0');
        const dop = (row['ДопШифр'] || '').trim();
        const base = dop ? shifr + '-' + dop : shifr;

        const jsonUrl = baseUrl + encodeURIComponent(base + '.json');
        let html = '<a href="' + jsonUrl + '" target="_blank" rel="noopener noreferrer">' + base + '.json</a>';

        const hasFile = row['РазмерАрхива'] > 0 && row['ТипФайла'] && !row['Скрыт'];
        if (hasFile) {
            const ext = row['ТипФайла'];
            const fileUrl = baseUrl + encodeURIComponent(base + '.' + ext);
            html += '<a href="' + fileUrl + '" target="_blank" rel="noopener noreferrer">' + base + '.' + ext + '</a>';
        }

        resultEl.innerHTML = html;
        resultEl.classList.add('visible');
    } catch (_) {
        resultEl.textContent = 'Ошибка при получении данных';
        resultEl.classList.add('visible');
    }
}

document.addEventListener('DOMContentLoaded', function () {
    document.querySelector('form').addEventListener('submit', function (e) {
        e.preventDefault();
        makeLinks();
    });
});
