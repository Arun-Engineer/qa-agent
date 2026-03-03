"""
Phase 5 · File Connector
Unified file operations across OneDrive, Google Drive, and S3 via MCP.
"""

import io
import json
import logging
import os
from dataclasses import dataclass
from enum import Enum
from typing import Any, Dict, List, Optional
from datetime import datetime

import aiohttp

logger = logging.getLogger(__name__)


class StorageProvider(Enum):
    ONEDRIVE = "onedrive"
    GDRIVE = "gdrive"
    S3 = "s3"


@dataclass
class FileMetadata:
    """Normalised file reference across all storage providers."""
    provider: StorageProvider
    file_id: str
    name: str
    path: str
    mime_type: str = ""
    size_bytes: int = 0
    url: Optional[str] = None
    created: Optional[datetime] = None
    modified: Optional[datetime] = None
    raw: Dict[str, Any] = None

    def __post_init__(self):
        if self.raw is None:
            self.raw = {}


class FileConnectorError(Exception):
    def __init__(self, message: str, provider: Optional[str] = None, raw: Any = None):
        super().__init__(message)
        self.provider = provider
        self.raw = raw


class FileConnector:
    """
    Unified file operations for OneDrive, Google Drive, and S3.
    Each provider can be enabled independently via config.
    """

    def __init__(self, config: Dict[str, Any]):
        self._providers: Dict[StorageProvider, Any] = {}
        self._session: Optional[aiohttp.ClientSession] = None

        if config.get("onedrive"):
            self._providers[StorageProvider.ONEDRIVE] = OneDriveClient(config["onedrive"])
        if config.get("gdrive"):
            self._providers[StorageProvider.GDRIVE] = GDriveClient(config["gdrive"])
        if config.get("s3"):
            self._providers[StorageProvider.S3] = S3Client(config["s3"])

    @property
    def available_providers(self) -> List[StorageProvider]:
        return list(self._providers.keys())

    async def close(self):
        for p in self._providers.values():
            if hasattr(p, "close"):
                await p.close()

    # ── unified API ────────────────────────────────────────────

    async def upload(
        self,
        provider: StorageProvider,
        content: bytes,
        dest_path: str,
        mime_type: str = "application/octet-stream",
    ) -> FileMetadata:
        client = self._get_client(provider)
        return await client.upload(content, dest_path, mime_type)

    async def download(
        self, provider: StorageProvider, file_id_or_path: str,
    ) -> bytes:
        client = self._get_client(provider)
        return await client.download(file_id_or_path)

    async def list_files(
        self, provider: StorageProvider, folder_path: str = "/",
        max_results: int = 100,
    ) -> List[FileMetadata]:
        client = self._get_client(provider)
        return await client.list_files(folder_path, max_results)

    async def delete(
        self, provider: StorageProvider, file_id_or_path: str,
    ) -> bool:
        client = self._get_client(provider)
        return await client.delete(file_id_or_path)

    async def get_share_link(
        self, provider: StorageProvider, file_id_or_path: str,
        expiry_hours: int = 24,
    ) -> str:
        client = self._get_client(provider)
        return await client.get_share_link(file_id_or_path, expiry_hours)

    def _get_client(self, provider: StorageProvider):
        if provider not in self._providers:
            raise FileConnectorError(
                f"Provider {provider.value} not configured",
                provider=provider.value,
            )
        return self._providers[provider]


# ═══════════════════════════════════════════════════════════════
# Provider Clients
# ═══════════════════════════════════════════════════════════════

class OneDriveClient:
    """Microsoft Graph API for OneDrive file operations."""

    GRAPH_URL = "https://graph.microsoft.com/v1.0"

    def __init__(self, config: Dict[str, Any]):
        self.tenant_id = config["tenant_id"]
        self.client_id = config["client_id"]
        self.client_secret = config["client_secret"]
        self.drive_id = config.get("drive_id", "me")
        self._token: Optional[str] = None
        self._token_expiry: Optional[datetime] = None
        self._session: Optional[aiohttp.ClientSession] = None

    async def _ensure_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession()
        await self._ensure_token()
        return self._session

    async def _ensure_token(self):
        if self._token and self._token_expiry and datetime.utcnow() < self._token_expiry:
            return
        async with aiohttp.ClientSession() as s:
            resp = await s.post(
                f"https://login.microsoftonline.com/{self.tenant_id}/oauth2/v2.0/token",
                data={
                    "client_id": self.client_id,
                    "client_secret": self.client_secret,
                    "scope": "https://graph.microsoft.com/.default",
                    "grant_type": "client_credentials",
                },
            )
            data = await resp.json()
            self._token = data["access_token"]
            from datetime import timedelta
            self._token_expiry = datetime.utcnow() + timedelta(seconds=data.get("expires_in", 3600) - 60)

    def _headers(self) -> Dict[str, str]:
        return {"Authorization": f"Bearer {self._token}", "Content-Type": "application/json"}

    async def upload(self, content: bytes, dest_path: str, mime_type: str) -> FileMetadata:
        session = await self._ensure_session()
        path_encoded = dest_path.lstrip("/").replace("/", ":/")
        url = f"{self.GRAPH_URL}/drives/{self.drive_id}/root:/{path_encoded}:/content"
        async with session.put(url, data=content, headers={
            "Authorization": f"Bearer {self._token}",
            "Content-Type": mime_type,
        }) as resp:
            if resp.status >= 400:
                raise FileConnectorError(f"OneDrive upload failed: {resp.status}", "onedrive")
            data = await resp.json()
        return self._normalise(data)

    async def download(self, file_id_or_path: str) -> bytes:
        session = await self._ensure_session()
        if file_id_or_path.startswith("/"):
            path_encoded = file_id_or_path.lstrip("/").replace("/", ":/")
            url = f"{self.GRAPH_URL}/drives/{self.drive_id}/root:/{path_encoded}:/content"
        else:
            url = f"{self.GRAPH_URL}/drives/{self.drive_id}/items/{file_id_or_path}/content"
        async with session.get(url, headers=self._headers()) as resp:
            if resp.status >= 400:
                raise FileConnectorError(f"OneDrive download failed: {resp.status}", "onedrive")
            return await resp.read()

    async def list_files(self, folder_path: str, max_results: int) -> List[FileMetadata]:
        session = await self._ensure_session()
        if folder_path == "/":
            url = f"{self.GRAPH_URL}/drives/{self.drive_id}/root/children?$top={max_results}"
        else:
            path_encoded = folder_path.lstrip("/").replace("/", ":/")
            url = f"{self.GRAPH_URL}/drives/{self.drive_id}/root:/{path_encoded}:/children?$top={max_results}"
        async with session.get(url, headers=self._headers()) as resp:
            data = await resp.json()
        return [self._normalise(item) for item in data.get("value", [])]

    async def delete(self, file_id_or_path: str) -> bool:
        session = await self._ensure_session()
        if file_id_or_path.startswith("/"):
            path_encoded = file_id_or_path.lstrip("/").replace("/", ":/")
            url = f"{self.GRAPH_URL}/drives/{self.drive_id}/root:/{path_encoded}"
        else:
            url = f"{self.GRAPH_URL}/drives/{self.drive_id}/items/{file_id_or_path}"
        async with session.delete(url, headers=self._headers()) as resp:
            return resp.status == 204

    async def get_share_link(self, file_id_or_path: str, expiry_hours: int) -> str:
        session = await self._ensure_session()
        url = f"{self.GRAPH_URL}/drives/{self.drive_id}/items/{file_id_or_path}/createLink"
        async with session.post(url, json={
            "type": "view", "scope": "anonymous"
        }, headers=self._headers()) as resp:
            data = await resp.json()
        return data.get("link", {}).get("webUrl", "")

    async def close(self):
        if self._session and not self._session.closed:
            await self._session.close()

    def _normalise(self, item: Dict) -> FileMetadata:
        return FileMetadata(
            provider=StorageProvider.ONEDRIVE,
            file_id=item.get("id", ""),
            name=item.get("name", ""),
            path=item.get("parentReference", {}).get("path", "") + "/" + item.get("name", ""),
            mime_type=item.get("file", {}).get("mimeType", ""),
            size_bytes=item.get("size", 0),
            url=item.get("webUrl"),
            created=datetime.fromisoformat(item["createdDateTime"].rstrip("Z")) if "createdDateTime" in item else None,
            modified=datetime.fromisoformat(item["lastModifiedDateTime"].rstrip("Z")) if "lastModifiedDateTime" in item else None,
            raw=item,
        )


class GDriveClient:
    """Google Drive API v3 client."""

    API_URL = "https://www.googleapis.com/drive/v3"
    UPLOAD_URL = "https://www.googleapis.com/upload/drive/v3"

    def __init__(self, config: Dict[str, Any]):
        self.credentials_json = config.get("service_account_json", {})
        self.folder_id = config.get("root_folder_id")
        self._token: Optional[str] = None
        self._token_expiry: Optional[datetime] = None
        self._session: Optional[aiohttp.ClientSession] = None

    async def _ensure_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession()
        await self._ensure_token()
        return self._session

    async def _ensure_token(self):
        """Obtain OAuth token from service account credentials."""
        if self._token and self._token_expiry and datetime.utcnow() < self._token_expiry:
            return
        import jwt as pyjwt
        from datetime import timedelta
        now = datetime.utcnow()
        payload = {
            "iss": self.credentials_json["client_email"],
            "scope": "https://www.googleapis.com/auth/drive",
            "aud": "https://oauth2.googleapis.com/token",
            "iat": int(now.timestamp()),
            "exp": int((now + timedelta(hours=1)).timestamp()),
        }
        signed = pyjwt.encode(payload, self.credentials_json["private_key"], algorithm="RS256")
        async with aiohttp.ClientSession() as s:
            resp = await s.post("https://oauth2.googleapis.com/token", data={
                "grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer",
                "assertion": signed,
            })
            data = await resp.json()
        self._token = data["access_token"]
        self._token_expiry = now + timedelta(seconds=data.get("expires_in", 3600) - 60)

    def _headers(self) -> Dict[str, str]:
        return {"Authorization": f"Bearer {self._token}"}

    async def upload(self, content: bytes, dest_path: str, mime_type: str) -> FileMetadata:
        session = await self._ensure_session()
        name = dest_path.split("/")[-1]
        metadata = {"name": name, "mimeType": mime_type}
        if self.folder_id:
            metadata["parents"] = [self.folder_id]

        url = f"{self.UPLOAD_URL}/files?uploadType=multipart"
        form = aiohttp.FormData()
        form.add_field("metadata", json.dumps(metadata), content_type="application/json")
        form.add_field("file", content, content_type=mime_type, filename=name)

        async with session.post(url, data=form, headers=self._headers()) as resp:
            if resp.status >= 400:
                raise FileConnectorError(f"GDrive upload failed: {resp.status}", "gdrive")
            data = await resp.json()
        return FileMetadata(
            provider=StorageProvider.GDRIVE,
            file_id=data["id"], name=data.get("name", name),
            path=dest_path, mime_type=mime_type, size_bytes=len(content),
            url=f"https://drive.google.com/file/d/{data['id']}",
            raw=data,
        )

    async def download(self, file_id: str) -> bytes:
        session = await self._ensure_session()
        url = f"{self.API_URL}/files/{file_id}?alt=media"
        async with session.get(url, headers=self._headers()) as resp:
            if resp.status >= 400:
                raise FileConnectorError(f"GDrive download failed: {resp.status}", "gdrive")
            return await resp.read()

    async def list_files(self, folder_path: str, max_results: int) -> List[FileMetadata]:
        session = await self._ensure_session()
        q = f"'{self.folder_id}' in parents and trashed = false" if self.folder_id else "trashed = false"
        url = f"{self.API_URL}/files?q={q}&pageSize={max_results}&fields=files(id,name,mimeType,size,webViewLink,createdTime,modifiedTime)"
        async with session.get(url, headers=self._headers()) as resp:
            data = await resp.json()
        results = []
        for f in data.get("files", []):
            results.append(FileMetadata(
                provider=StorageProvider.GDRIVE,
                file_id=f["id"], name=f.get("name", ""),
                path=f"/{f.get('name', '')}",
                mime_type=f.get("mimeType", ""),
                size_bytes=int(f.get("size", 0)),
                url=f.get("webViewLink"),
                created=datetime.fromisoformat(f["createdTime"].rstrip("Z")) if "createdTime" in f else None,
                modified=datetime.fromisoformat(f["modifiedTime"].rstrip("Z")) if "modifiedTime" in f else None,
                raw=f,
            ))
        return results

    async def delete(self, file_id: str) -> bool:
        session = await self._ensure_session()
        async with session.delete(f"{self.API_URL}/files/{file_id}", headers=self._headers()) as resp:
            return resp.status == 204

    async def get_share_link(self, file_id: str, expiry_hours: int) -> str:
        session = await self._ensure_session()
        await session.post(f"{self.API_URL}/files/{file_id}/permissions", json={
            "role": "reader", "type": "anyone",
        }, headers={**self._headers(), "Content-Type": "application/json"})
        return f"https://drive.google.com/file/d/{file_id}/view?usp=sharing"

    async def close(self):
        if self._session and not self._session.closed:
            await self._session.close()


class S3Client:
    """AWS S3 client using boto3 (async wrapper)."""

    def __init__(self, config: Dict[str, Any]):
        self.bucket = config["bucket"]
        self.region = config.get("region", "us-east-1")
        self.prefix = config.get("prefix", "")
        self._access_key = config.get("access_key_id", os.getenv("AWS_ACCESS_KEY_ID", ""))
        self._secret_key = config.get("secret_access_key", os.getenv("AWS_SECRET_ACCESS_KEY", ""))
        self._client = None

    def _get_client(self):
        if self._client is None:
            import boto3
            self._client = boto3.client(
                "s3", region_name=self.region,
                aws_access_key_id=self._access_key,
                aws_secret_access_key=self._secret_key,
            )
        return self._client

    async def upload(self, content: bytes, dest_path: str, mime_type: str) -> FileMetadata:
        import asyncio
        key = self.prefix + dest_path.lstrip("/")
        client = self._get_client()
        await asyncio.get_event_loop().run_in_executor(
            None, lambda: client.put_object(
                Bucket=self.bucket, Key=key, Body=content, ContentType=mime_type,
            )
        )
        return FileMetadata(
            provider=StorageProvider.S3,
            file_id=key, name=dest_path.split("/")[-1],
            path=key, mime_type=mime_type, size_bytes=len(content),
            url=f"https://{self.bucket}.s3.{self.region}.amazonaws.com/{key}",
            raw={"bucket": self.bucket, "key": key},
        )

    async def download(self, key: str) -> bytes:
        import asyncio
        client = self._get_client()
        resp = await asyncio.get_event_loop().run_in_executor(
            None, lambda: client.get_object(Bucket=self.bucket, Key=key)
        )
        return resp["Body"].read()

    async def list_files(self, folder_path: str, max_results: int) -> List[FileMetadata]:
        import asyncio
        client = self._get_client()
        prefix = self.prefix + folder_path.lstrip("/")
        resp = await asyncio.get_event_loop().run_in_executor(
            None, lambda: client.list_objects_v2(
                Bucket=self.bucket, Prefix=prefix, MaxKeys=max_results,
            )
        )
        results = []
        for obj in resp.get("Contents", []):
            results.append(FileMetadata(
                provider=StorageProvider.S3,
                file_id=obj["Key"], name=obj["Key"].split("/")[-1],
                path=obj["Key"], size_bytes=obj.get("Size", 0),
                modified=obj.get("LastModified"),
                url=f"https://{self.bucket}.s3.{self.region}.amazonaws.com/{obj['Key']}",
                raw=obj,
            ))
        return results

    async def delete(self, key: str) -> bool:
        import asyncio
        client = self._get_client()
        await asyncio.get_event_loop().run_in_executor(
            None, lambda: client.delete_object(Bucket=self.bucket, Key=key)
        )
        return True

    async def get_share_link(self, key: str, expiry_hours: int) -> str:
        import asyncio
        client = self._get_client()
        url = await asyncio.get_event_loop().run_in_executor(
            None, lambda: client.generate_presigned_url(
                "get_object",
                Params={"Bucket": self.bucket, "Key": key},
                ExpiresIn=expiry_hours * 3600,
            )
        )
        return url

    async def close(self):
        pass  # boto3 doesn't need explicit close