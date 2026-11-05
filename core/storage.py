"""Penyimpanan file: Google Drive (produksi) atau folder lokal (uji coba).

Semua modul memakai antarmuka yang sama, sehingga bot bisa diuji di komputer
tanpa menyentuh Google Drive (STORAGE=local).
"""
import hashlib
import io
import json
import logging
import shutil
import time
from pathlib import Path

from core import config as cfg

log = logging.getLogger(__name__)


class Storage:
    # --- dipakai oleh semua backend --------------------------------------
    def read_json(self, folder_path: str, name: str, default=None):
        f = self.find(self.folder(folder_path), name)
        if not f:
            return default
        tmp = cfg.WORK_DIR / f"_read_{name}"
        self.download(f["id"], tmp)
        try:
            return json.loads(tmp.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            log.error("File %s/%s rusak, memakai nilai awal.", folder_path, name)
            return default

    def write_json(self, folder_path: str, name: str, data) -> None:
        tmp = cfg.WORK_DIR / f"_write_{name}"
        tmp.write_text(json.dumps(data, indent=1, ensure_ascii=False, default=str), encoding="utf-8")
        self.upload(tmp, self.folder(folder_path), name, "application/json")

    def read_text(self, folder_path: str, name: str) -> str | None:
        f = self.find(self.folder(folder_path), name)
        if not f:
            return None
        tmp = cfg.WORK_DIR / f"_read_{name}"
        self.download(f["id"], tmp)
        return tmp.read_text(encoding="utf-8")


# ============================================================ Lokal
class LocalStorage(Storage):
    def __init__(self, root: Path):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def folder(self, path: str, create: bool = True) -> str:
        p = self.root / path if path else self.root
        if create:
            p.mkdir(parents=True, exist_ok=True)
        return str(p)

    def _info(self, p: Path) -> dict:
        info = {"id": str(p), "name": p.name, "is_folder": p.is_dir(), "link": p.as_uri()}
        if p.is_file():
            info["md5"] = hashlib.md5(p.read_bytes()).hexdigest()
            info["size"] = p.stat().st_size
        return info

    def list(self, folder_id: str) -> list[dict]:
        p = Path(folder_id)
        return [self._info(c) for c in sorted(p.iterdir())] if p.exists() else []

    def find(self, folder_id: str, name: str) -> dict | None:
        p = Path(folder_id) / name
        return self._info(p) if p.exists() else None

    def upload(self, local, folder_id, name=None, mime=None) -> dict:
        dest = Path(folder_id) / (name or Path(local).name)
        if Path(local).resolve() != dest.resolve():
            shutil.copyfile(local, dest)
        return self._info(dest)

    def download(self, file_id, local) -> Path:
        shutil.copyfile(file_id, local)
        return Path(local)

    def move(self, file_id, dest_folder_id, src_folder_id=None, new_name=None) -> dict:
        src = Path(file_id)
        dest = Path(dest_folder_id) / (new_name or src.name)
        if dest.exists():
            dest.unlink()
        shutil.move(str(src), dest)
        return self._info(dest)

    def folder_link(self, folder_id) -> str:
        return Path(folder_id).as_uri()


# ============================================================ Google Drive
FOLDER_MIME = "application/vnd.google-apps.folder"


class DriveStorage(Storage):
    def __init__(self, root_id: str, token_json: str):
        from google.auth.transport.requests import Request
        from google.oauth2.credentials import Credentials
        from googleapiclient.discovery import build

        info = json.loads(token_json)
        creds = Credentials.from_authorized_user_info(info, ["https://www.googleapis.com/auth/drive"])
        if not creds.valid:
            creds.refresh(Request())
        self.svc = build("drive", "v3", credentials=creds, cache_discovery=False)
        self.root_id = root_id
        self._cache: dict[str, str] = {"": root_id}

    def _exec(self, req, tries=5):
        for i in range(tries):
            try:
                return req.execute()
            except Exception as e:  # kuota/jaringan: coba lagi dengan jeda
                if i == tries - 1:
                    raise
                log.warning("Drive API error (%s), mencoba lagi…", e)
                time.sleep(2 ** i)

    @staticmethod
    def _esc(s: str) -> str:
        return s.replace("\\", "\\\\").replace("'", "\\'")

    def _norm(self, f: dict) -> dict:
        return {"id": f["id"], "name": f["name"], "is_folder": f["mimeType"] == FOLDER_MIME,
                "md5": f.get("md5Checksum"), "size": int(f.get("size", 0) or 0),
                "link": f.get("webViewLink", "")}

    def folder(self, path: str, create: bool = True) -> str | None:
        path = path.strip("/")
        if path in self._cache:
            return self._cache[path]
        parent_path, _, name = path.rpartition("/")
        parent = self.folder(parent_path, create)
        if parent is None:
            return None
        found = self.find(parent, name, folders_only=True)
        if found:
            fid = found["id"]
        elif create:
            fid = self._exec(self.svc.files().create(
                body={"name": name, "mimeType": FOLDER_MIME, "parents": [parent]},
                fields="id", supportsAllDrives=True))["id"]
        else:
            return None
        self._cache[path] = fid
        return fid

    def list(self, folder_id: str) -> list[dict]:
        out, token = [], None
        while True:
            res = self._exec(self.svc.files().list(
                q=f"'{folder_id}' in parents and trashed = false",
                fields="nextPageToken, files(id,name,mimeType,md5Checksum,size,webViewLink)",
                pageSize=500, pageToken=token, supportsAllDrives=True, includeItemsFromAllDrives=True))
            out += [self._norm(f) for f in res.get("files", [])]
            token = res.get("nextPageToken")
            if not token:
                return sorted(out, key=lambda x: x["name"])

    def find(self, folder_id: str, name: str, folders_only: bool = False) -> dict | None:
        q = f"name = '{self._esc(name)}' and '{folder_id}' in parents and trashed = false"
        if folders_only:
            q += f" and mimeType = '{FOLDER_MIME}'"
        res = self._exec(self.svc.files().list(
            q=q, fields="files(id,name,mimeType,md5Checksum,size,webViewLink)", pageSize=10,
            supportsAllDrives=True, includeItemsFromAllDrives=True))
        files = res.get("files", [])
        return self._norm(files[0]) if files else None

    def upload(self, local, folder_id, name=None, mime=None) -> dict:
        from googleapiclient.http import MediaFileUpload
        name = name or Path(local).name
        existing = self.find(folder_id, name)
        media = MediaFileUpload(str(local), mimetype=mime, resumable=True)
        fields = "id,name,mimeType,md5Checksum,size,webViewLink"
        if existing and not existing["is_folder"]:
            f = self._exec(self.svc.files().update(fileId=existing["id"], media_body=media,
                                                    fields=fields, supportsAllDrives=True))
        else:
            f = self._exec(self.svc.files().create(body={"name": name, "parents": [folder_id]},
                                                    media_body=media, fields=fields, supportsAllDrives=True))
        return self._norm(f)

    def download(self, file_id, local) -> Path:
        from googleapiclient.http import MediaIoBaseDownload
        buf = io.BytesIO()
        dl = MediaIoBaseDownload(buf, self.svc.files().get_media(fileId=file_id, supportsAllDrives=True))
        done = False
        while not done:
            _, done = dl.next_chunk()
        Path(local).write_bytes(buf.getvalue())
        return Path(local)

    def move(self, file_id, dest_folder_id, src_folder_id=None, new_name=None) -> dict:
        if src_folder_id is None:
            meta = self._exec(self.svc.files().get(fileId=file_id, fields="parents", supportsAllDrives=True))
            src_folder_id = ",".join(meta.get("parents", []))
        body = {"name": new_name} if new_name else {}
        f = self._exec(self.svc.files().update(
            fileId=file_id, body=body, addParents=dest_folder_id, removeParents=src_folder_id,
            fields="id,name,mimeType,md5Checksum,size,webViewLink", supportsAllDrives=True))
        return self._norm(f)

    def folder_link(self, folder_id) -> str:
        return f"https://drive.google.com/drive/folders/{folder_id}"


# ============================================================ Jembatan Apps Script (tanpa Google Cloud Console)
MAX_BRIDGE_BYTES = 35 * 1024 * 1024   # batas aman ukuran file lewat Apps Script


class BridgeStorage(Storage):
    def __init__(self, url: str, key: str):
        import requests
        self.http = requests.Session()
        self.url, self.key = url, key
        self._cache: dict[str, str] = {}

    def _call(self, action: str, **params):
        payload = {"key": self.key, "action": action, **params}
        last = None
        for i in range(5):
            try:
                r = self.http.post(self.url, json=payload, timeout=360, allow_redirects=True)
                if r.status_code >= 500 or r.status_code == 429:
                    raise RuntimeError(f"HTTP {r.status_code}")
                data = r.json()
            except Exception as e:   # jaringan / Apps Script sibuk: coba lagi dengan jeda
                last = e
                time.sleep(2 ** i)
                continue
            if not data.get("ok"):
                raise RuntimeError(f"Jembatan Drive menolak '{action}': {data.get('error')}")
            return data.get("result")
        raise RuntimeError(f"Jembatan Drive tidak merespons ({action}): {last}")

    def folder(self, path: str, create: bool = True) -> str | None:
        path = path.strip("/")
        if path not in self._cache:
            fid = self._call("folder", path=path, create=create)
            if fid is None:
                return None
            self._cache[path] = fid
        return self._cache[path]

    def list(self, folder_id: str) -> list[dict]:
        return sorted(self._call("list", folder_id=folder_id), key=lambda x: x["name"])

    def find(self, folder_id: str, name: str, folders_only: bool = False) -> dict | None:
        return self._call("find", folder_id=folder_id, name=name, folders_only=folders_only)

    def upload(self, local, folder_id, name=None, mime=None) -> dict:
        import base64
        data = Path(local).read_bytes()
        if len(data) > MAX_BRIDGE_BYTES:
            raise RuntimeError(f"File {Path(local).name} terlalu besar untuk jembatan Apps Script (>35 MB).")
        return self._call("upload", folder_id=folder_id, name=name or Path(local).name, mime=mime,
                          data=base64.b64encode(data).decode())

    def download(self, file_id, local) -> Path:
        import base64
        Path(local).write_bytes(base64.b64decode(self._call("download", file_id=file_id)))
        return Path(local)

    def move(self, file_id, dest_folder_id, src_folder_id=None, new_name=None) -> dict:
        return self._call("move", file_id=file_id, dest_folder_id=dest_folder_id,
                          src_folder_id=src_folder_id, new_name=new_name)

    def folder_link(self, folder_id) -> str:
        return f"https://drive.google.com/drive/folders/{folder_id}"


_storage = None


def get_storage() -> Storage:
    global _storage
    if _storage is None:
        if cfg.STORAGE == "local":
            _storage = LocalStorage(cfg.LOCAL_ROOT)
        elif cfg.STORAGE == "bridge":
            if not (cfg.BRIDGE_URL and cfg.BRIDGE_KEY):
                raise SystemExit("BRIDGE_URL / BRIDGE_KEY belum diisi di GitHub Secrets.")
            _storage = BridgeStorage(cfg.BRIDGE_URL, cfg.BRIDGE_KEY)
        else:
            if not (cfg.DRIVE_ROOT_FOLDER_ID and cfg.GOOGLE_TOKEN):
                raise SystemExit("DRIVE_ROOT_FOLDER_ID / GOOGLE_TOKEN belum diisi di GitHub Secrets.")
            _storage = DriveStorage(cfg.DRIVE_ROOT_FOLDER_ID, cfg.GOOGLE_TOKEN)
    return _storage
