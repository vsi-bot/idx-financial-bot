/**
 * Pemicu & penjadwal cadangan bot (Google Apps Script, gratis).
 * Berjalan tiap 5 menit. Selama pukul 07.00 - 17.30 WIB, bot di GitHub dipicu bila:
 *   1. ada file baru di folder Inbox, ATAU
 *   2. masih ada laporan di antrean _Sistem/outbox yang belum terkirim, ATAU
 *   3. bot belum dipicu lebih dari 20 menit (cadangan bila jadwal GitHub terlambat/terlewat).
 * Setelah memicu, menunggu minimal 10 menit sebelum memicu lagi.
 *
 * Script properties: ROOT_FOLDER_ID, GITHUB_REPO (mis. vsi-bot/idx-financial-bot), GITHUB_TOKEN
 * Jalankan fungsi setup() satu kali untuk memasang pemicu.
 */
const PROPS = PropertiesService.getScriptProperties();
const COOLDOWN_MIN = 10;     // jeda minimal antar-pemicu
const PACEMAKER_MIN = 20;    // picu bot bila sudah selama ini tidak dipicu

/** Bot hanya dipicu pukul 07.00 - 17.30 WIB; file di luar jam itu diproses pada putaran 07.00. */
function inWindow_() {
  const hm = Utilities.formatDate(new Date(), 'Asia/Jakarta', 'HH:mm');
  return hm >= '07:00' && hm <= '17:30';
}

function checkInbox() {
  if (!inWindow_()) return;
  const root = DriveApp.getFolderById(PROPS.getProperty('ROOT_FOLDER_ID'));
  const last = Number(PROPS.getProperty('LAST_DISPATCH') || 0);
  const minutesSince = (Date.now() - last) / 60000;
  if (minutesSince < COOLDOWN_MIN) return;

  let reason = null;
  const inbox = subFolder_(root, 'Inbox');
  if (inbox && hasPendingFiles_(inbox)) {
    reason = 'file baru di Inbox';
  } else if (hasPendingOutbox_(root)) {
    reason = 'antrean laporan belum terkirim';
  } else if (minutesSince >= PACEMAKER_MIN) {
    reason = 'jadwal cadangan (' + Math.round(minutesSince) + ' menit sejak pemicu terakhir)';
  }
  if (!reason) return;

  const res = UrlFetchApp.fetch('https://api.github.com/repos/' + PROPS.getProperty('GITHUB_REPO') + '/dispatches', {
    method: 'post',
    contentType: 'application/json',
    headers: {
      Authorization: 'Bearer ' + PROPS.getProperty('GITHUB_TOKEN'),
      Accept: 'application/vnd.github+json'
    },
    payload: JSON.stringify({ event_type: 'inbox' }),
    muteHttpExceptions: true
  });
  if (res.getResponseCode() === 204) {
    PROPS.setProperty('LAST_DISPATCH', String(Date.now()));
    console.log('Bot dipicu: ' + reason + '.');
  } else {
    console.log('GitHub menolak (' + res.getResponseCode() + '): ' + res.getContentText());
  }
}

function subFolder_(parent, name) {
  const it = parent.getFoldersByName(name);
  return it.hasNext() ? it.next() : null;
}

function hasPendingFiles_(inbox) {
  if (inbox.getFiles().hasNext()) return true;
  const subs = inbox.getFolders();
  while (subs.hasNext()) {
    const f = subs.next();
    if (f.getName().charAt(0) === '_') continue;   // _Tidak Dikenali, _Duplikat diabaikan
    if (f.getFiles().hasNext()) return true;
  }
  return false;
}

/** Ada laporan di _Sistem/outbox (file .json) yang belum dikirim ke Discord? */
function hasPendingOutbox_(root) {
  const sys = subFolder_(root, '_Sistem');
  const outbox = sys ? subFolder_(sys, 'outbox') : null;
  if (!outbox) return false;
  const files = outbox.getFiles();
  while (files.hasNext()) {
    if (files.next().getName().toLowerCase().endsWith('.json')) return true;
  }
  return false;
}

/** Jalankan SEKALI untuk memasang pemicu otomatis tiap 5 menit. */
function setup() {
  ScriptApp.getProjectTriggers().forEach(function (t) { ScriptApp.deleteTrigger(t); });
  ScriptApp.newTrigger('checkInbox').timeBased().everyMinutes(5).create();
  console.log('Pemicu terpasang. checkInbox akan berjalan tiap 5 menit.');
}

/** Tes manual: jalankan fungsi ini untuk memicu bot sekarang juga (abaikan jam & jeda). */
function testDispatch() {
  PROPS.deleteProperty('LAST_DISPATCH');
  const res = UrlFetchApp.fetch('https://api.github.com/repos/' + PROPS.getProperty('GITHUB_REPO') + '/dispatches', {
    method: 'post', contentType: 'application/json',
    headers: { Authorization: 'Bearer ' + PROPS.getProperty('GITHUB_TOKEN'), Accept: 'application/vnd.github+json' },
    payload: JSON.stringify({ event_type: 'inbox' }), muteHttpExceptions: true
  });
  console.log(res.getResponseCode() === 204 ? 'Berhasil: cek tab Actions di GitHub.' :
              'GitHub menolak (' + res.getResponseCode() + '): ' + res.getContentText());
}
