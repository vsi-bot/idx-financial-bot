/**
 * Jembatan Google Drive untuk bot (pengganti Google Cloud Console).
 * Dipasang sebagai Web App di project Apps Script yang sama dengan InboxWatcher.gs.
 *
 * Script properties yang dibutuhkan:
 *   ROOT_FOLDER_ID : ID folder induk Google Drive
 *   BRIDGE_KEY     : kata sandi panjang buatan Anda (sama dengan GitHub Secret BRIDGE_KEY)
 *
 * Wajib: aktifkan layanan "Drive API" di menu Services (ikon + di samping "Services").
 */
const BRIDGE_PROPS = PropertiesService.getScriptProperties();
const BRIDGE_FOLDER_MIME = 'application/vnd.google-apps.folder';
const BRIDGE_FIELDS = 'id,name,mimeType,md5Checksum,size,webViewLink';

function doPost(e) {
  let req;
  try {
    req = JSON.parse(e.postData.contents);
  } catch (err) {
    return bridgeOut_({ ok: false, error: 'permintaan tidak valid' });
  }
  const key = BRIDGE_PROPS.getProperty('BRIDGE_KEY');
  if (!key || req.key !== key) return bridgeOut_({ ok: false, error: 'unauthorized' });
  const fn = BRIDGE_ACTIONS_[req.action];
  if (!fn) return bridgeOut_({ ok: false, error: 'aksi tidak dikenal: ' + req.action });
  try {
    return bridgeOut_({ ok: true, result: fn(req) });
  } catch (err) {
    return bridgeOut_({ ok: false, error: String(err) });
  }
}

function doGet() {
  return bridgeOut_({ ok: true, result: 'Jembatan Drive aktif.' });
}

function bridgeOut_(obj) {
  return ContentService.createTextOutput(JSON.stringify(obj)).setMimeType(ContentService.MimeType.JSON);
}

function bridgeEsc_(s) {
  return String(s).replace(/\\/g, '\\\\').replace(/'/g, "\\'");
}

function bridgeNorm_(f) {
  return {
    id: f.id, name: f.name, is_folder: f.mimeType === BRIDGE_FOLDER_MIME,
    md5: f.md5Checksum || null, size: Number(f.size || 0),
    link: f.webViewLink || ''
  };
}

function bridgeList_(parentId, extraQuery) {
  const out = [];
  let token = null;
  do {
    const res = Drive.Files.list({
      q: "'" + parentId + "' in parents and trashed = false" + (extraQuery || ''),
      fields: 'nextPageToken, files(' + BRIDGE_FIELDS + ')',
      pageSize: 500, pageToken: token,
      supportsAllDrives: true, includeItemsFromAllDrives: true
    });
    (res.files || []).forEach(function (f) { out.push(bridgeNorm_(f)); });
    token = res.nextPageToken;
  } while (token);
  return out;
}

function bridgeGet_(id) {
  return bridgeNorm_(Drive.Files.get(id, { fields: BRIDGE_FIELDS, supportsAllDrives: true }));
}

const BRIDGE_ACTIONS_ = {
  // Pastikan jalur folder ada (dibuat bila belum), kembalikan ID folder terakhir.
  folder: function (r) {
    let id = BRIDGE_PROPS.getProperty('ROOT_FOLDER_ID');
    const parts = String(r.path || '').split('/').filter(function (p) { return p; });
    for (let i = 0; i < parts.length; i++) {
      const hit = bridgeList_(id, " and mimeType = '" + BRIDGE_FOLDER_MIME + "' and name = '" + bridgeEsc_(parts[i]) + "'");
      if (hit.length) {
        id = hit[0].id;
      } else if (r.create === false) {
        return null;
      } else {
        id = DriveApp.getFolderById(id).createFolder(parts[i]).getId();
      }
    }
    return id;
  },

  list: function (r) {
    return bridgeList_(r.folder_id);
  },

  find: function (r) {
    let q = " and name = '" + bridgeEsc_(r.name) + "'";
    if (r.folders_only) q += " and mimeType = '" + BRIDGE_FOLDER_MIME + "'";
    const hit = bridgeList_(r.folder_id, q);
    return hit.length ? hit[0] : null;
  },

  // Unggah file (base64). Jika nama yang sama sudah ada, isinya ditimpa (link tetap).
  upload: function (r) {
    const blob = Utilities.newBlob(Utilities.base64Decode(r.data), r.mime || 'application/octet-stream', r.name);
    const existing = bridgeList_(r.folder_id, " and name = '" + bridgeEsc_(r.name) + "' and mimeType != '" + BRIDGE_FOLDER_MIME + "'");
    if (existing.length) {
      Drive.Files.update({}, existing[0].id, blob, { supportsAllDrives: true });
      return bridgeGet_(existing[0].id);
    }
    return bridgeGet_(DriveApp.getFolderById(r.folder_id).createFile(blob).getId());
  },

  download: function (r) {
    return Utilities.base64Encode(DriveApp.getFileById(r.file_id).getBlob().getBytes());
  },

  move: function (r) {
    const file = DriveApp.getFileById(r.file_id);
    if (r.new_name) file.setName(r.new_name);
    if (!r.src_folder_id || r.src_folder_id !== r.dest_folder_id) {
      file.moveTo(DriveApp.getFolderById(r.dest_folder_id));
    }
    return bridgeGet_(r.file_id);
  }
};
