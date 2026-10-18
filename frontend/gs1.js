'use strict';

(function () {
  const FIXED_LENGTH = { '01': 14, '11': 6, '15': 6, '17': 6 };
  const VARIABLE = ['10', '21'];

  function splitPairs(source) {
    const pairs = [];
    if (/\(\d{2,4}\)/.test(source)) {
      for (const match of source.matchAll(/\((\d{2,4})\)([^(]*)/g)) {
        pairs.push([match[1], match[2].trim()]);
      }
      return pairs;
    }
    const clean = source.replace(/^\]d2/i, '');
    let pos = 0;
    while (pos < clean.length) {
      const ai = clean.slice(pos, pos + 2);
      if (FIXED_LENGTH[ai]) {
        pairs.push([ai, clean.slice(pos + 2, pos + 2 + FIXED_LENGTH[ai])]);
        pos += 2 + FIXED_LENGTH[ai];
      } else if (VARIABLE.includes(ai)) {
        let end = clean.indexOf('\u001d', pos + 2);
        if (end === -1) end = clean.length;
        pairs.push([ai, clean.slice(pos + 2, end)]);
        pos = end + 1;
      } else {
        break;
      }
    }
    return pairs;
  }

  function toIsoDate(value) {
    if (!/^\d{6}$/.test(value)) return null;
    const year = 2000 + Number(value.slice(0, 2));
    const month = Number(value.slice(2, 4));
    let day = Number(value.slice(4, 6));
    if (month < 1 || month > 12) return null;
    if (day === 0) day = new Date(year, month, 0).getDate();
    return `${year}-${String(month).padStart(2, '0')}-${String(day).padStart(2, '0')}`;
  }

  function parse(text) {
    const result = {};
    splitPairs(String(text || '').trim()).forEach(([ai, value]) => {
      if (ai === '01' && /^\d{14}$/.test(value)) result.gtin = value;
      if (ai === '21' && value) result.serial = value;
      if (ai === '10' && value) result.lot = value;
      if (ai === '17') {
        const iso = toIsoDate(value);
        if (iso) result.expiration = iso;
      }
    });
    return result;
  }

  function isGs1(data) {
    return Boolean(data.gtin || data.serial || data.expiration || data.lot);
  }

  function scannerFormats() {
    const f = window.Html5QrcodeSupportedFormats;
    if (!f) return undefined;
    return [
      f.QR_CODE, f.DATA_MATRIX, f.EAN_13, f.EAN_8, f.UPC_A, f.UPC_E,
      f.CODE_128, f.CODE_39, f.CODE_93, f.ITF
    ].filter((format) => Number.isInteger(format));
  }

  window.Gs1 = { parse, isGs1, scannerFormats };
})();
