/**
 * Pemantau folder Inbox (Google Apps Script, gratis).
 * Setiap 5 menit memeriksa folder Inbox. Jika ada file baru, memicu GitHub Actions.
 *
 * Isi 3 "Script properties" (Project Settings > Script properties):
 *   ROOT_FOLDER_ID : ID folder induk Google Drive (sama dengan DRIVE_ROOT_FOLDER_ID)
 *   GITHUB_REPO    : namaakun/namarepo, contoh: budi/idx-financial-bot
 *   GITHUB_TOKEN   : fine-grained token GitHub (izin Contents: Read and write) untuk repo tersebut
 *
 * Lalu jalankan fungsi setup() satu kali.
 */
const PROPS = PropertiesService.getScriptProperties();

/** Bot hanya dipicu pukul 07.00 - 17.30 WIB; file di luar jam itu diproses pada putaran 07.00. */
function inWindow_() {
  const hm = Utilities.formatDate(new Date(), 'Asia/Jakarta', 'HH:mm');
  return hm >= '07:00' && hm <= '17:30';
}

function checkInbox() {
  if (!inWindow_()) return;
  const root = DriveApp.getFolderById(PROPS.getProperty('ROOT_FOLDER_ID'));
  const it = root.getFoldersByName('Inbox');
  if (!it.hasNext()) return;
  if (!hasPendingFiles_(it.next())) return;

  // beri waktu bot menyelesaikan proses sebelumnya
  const last = Number(PROPS.getProperty('LAST_DISPATCH') || 0);
  if (Date.now() - last < 10 * 60 * 1000) return;

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
    console.log('Bot dipicu: ada file baru di Inbox.');
  } else {
    console.log('GitHub menolak (' + res.getResponseCode() + '): ' + res.getContentText());
  }
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

/** Jalankan SEKALI untuk memasang pemicu otomatis tiap 5 menit. */
function setup() {
  ScriptApp.getProjectTriggers().forEach(function (t) { ScriptApp.deleteTrigger(t); });
  ScriptApp.newTrigger('checkInbox').timeBased().everyMinutes(5).create();
  console.log('Pemicu terpasang. checkInbox akan berjalan tiap 5 menit.');
}
