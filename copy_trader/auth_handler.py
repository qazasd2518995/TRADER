"""
黃金跟單系統 — DynamoDB 認證模組
Table: Gold, Partition Key: Gold (String), Region: ap-southeast-2
"""
import os
import logging
from datetime import datetime, timezone

import boto3
import bcrypt
from boto3.dynamodb.conditions import Key

logger = logging.getLogger(__name__)

# DynamoDB 設定
_REGION = "ap-southeast-2"
_TABLE_NAME = "Gold"
_PK_FIELD = "Gold"
_USER_PREFIX = "USER#"

# 不回傳給前端的欄位
_HIDDEN_FIELDS = {"password_hash"}

# 使用者可見欄位
_USER_FIELDS = (
    "email", "display_name", "plan", "status",
    "starts_at", "expires_at", "is_admin", "created_at", "notes",
)


class AuthHandler:
    """DynamoDB-based authentication and subscription management."""

    def __init__(self):
        self.dynamodb = boto3.resource(
            "dynamodb",
            region_name=_REGION,
            aws_access_key_id=os.environ.get("AWS_ACCESS_KEY_ID", ""),
            aws_secret_access_key=os.environ.get("AWS_SECRET_ACCESS_KEY", ""),
        )
        self.table = self.dynamodb.Table(_TABLE_NAME)
        logger.info("AuthHandler initialized (region=%s, table=%s)", _REGION, _TABLE_NAME)

    # ── helpers ──────────────────────────────────────────────

    @staticmethod
    def _user_key(email: str) -> str:
        return f"{_USER_PREFIX}{email}"

    @staticmethod
    def _hash_password(password: str) -> str:
        return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")

    @staticmethod
    def _check_password(password: str, password_hash: str) -> bool:
        return bcrypt.checkpw(password.encode("utf-8"), password_hash.encode("utf-8"))

    def _get_user_raw(self, email: str) -> dict | None:
        resp = self.table.get_item(Key={_PK_FIELD: self._user_key(email)})
        return resp.get("Item")

    @staticmethod
    def _sanitize(item: dict) -> dict:
        """Strip hidden fields and PK from user record."""
        return {k: v for k, v in item.items() if k not in _HIDDEN_FIELDS and k != _PK_FIELD}

    @staticmethod
    def _is_subscription_valid(item: dict) -> bool:
        if item.get("status") != "active":
            return False
        expires = item.get("expires_at", "")
        if not expires:
            return False
        try:
            exp_dt = datetime.fromisoformat(expires.replace("Z", "+00:00"))
            return datetime.now(timezone.utc) < exp_dt
        except (ValueError, TypeError):
            return False

    def _assert_admin(self, admin_email: str):
        """Raise if caller is not an admin."""
        item = self._get_user_raw(admin_email)
        if not item or not item.get("is_admin"):
            raise PermissionError("需要管理員權限")

    # ── public API ───────────────────────────────────────────

    def login(self, email: str, password: str) -> dict:
        item = self._get_user_raw(email)
        if not item:
            return {"error": "帳號或密碼錯誤"}

        if not self._check_password(password, item.get("password_hash", "")):
            return {"error": "帳號或密碼錯誤"}

        user = self._sanitize(item)
        user["subscription_valid"] = self._is_subscription_valid(item)
        return {"user": user}

    def verify_subscription(self, email: str) -> dict:
        item = self._get_user_raw(email)
        if not item:
            return {"valid": False, "error": "使用者不存在"}

        valid = self._is_subscription_valid(item)
        return {
            "valid": valid,
            "plan": item.get("plan", ""),
            "status": item.get("status", ""),
            "expires_at": item.get("expires_at", ""),
        }

    def change_password(self, email: str, old_password: str, new_password: str) -> dict:
        item = self._get_user_raw(email)
        if not item:
            return {"error": "使用者不存在"}

        if not self._check_password(old_password, item.get("password_hash", "")):
            return {"error": "舊密碼錯誤"}

        new_hash = self._hash_password(new_password)
        self.table.update_item(
            Key={_PK_FIELD: self._user_key(email)},
            UpdateExpression="SET password_hash = :h",
            ExpressionAttributeValues={":h": new_hash},
        )
        return {"status": "ok"}

    # ── admin ────────────────────────────────────────────────

    def admin_list_users(self, admin_email: str) -> dict:
        self._assert_admin(admin_email)

        resp = self.table.scan(
            FilterExpression=Key(_PK_FIELD).begins_with(_USER_PREFIX),
        )
        users = [self._sanitize(item) for item in resp.get("Items", [])]
        return {"users": users}

    def admin_create_user(self, admin_email: str, user_data: dict) -> dict:
        self._assert_admin(admin_email)

        email = user_data.get("email", "").strip().lower()
        password = user_data.get("password", "")
        if not email or not password:
            return {"error": "email 和密碼為必填"}

        # Check if user already exists
        if self._get_user_raw(email):
            return {"error": f"使用者 {email} 已存在"}

        now = datetime.now(timezone.utc).isoformat()
        item = {
            _PK_FIELD: self._user_key(email),
            "email": email,
            "password_hash": self._hash_password(password),
            "display_name": user_data.get("display_name", email.split("@")[0]),
            "is_admin": user_data.get("is_admin", False),
            "plan": user_data.get("plan", "trial"),
            "status": "active",
            "starts_at": now,
            "expires_at": user_data.get("expires_at", ""),
            "created_at": now,
            "notes": user_data.get("notes", ""),
        }
        self.table.put_item(Item=item)
        return {"status": "ok", "user": self._sanitize(item)}

    def admin_update_user(self, admin_email: str, email: str, updates: dict) -> dict:
        self._assert_admin(admin_email)

        item = self._get_user_raw(email)
        if not item:
            return {"error": f"使用者 {email} 不存在"}

        allowed = {"plan", "status", "expires_at", "notes", "display_name", "is_admin"}
        expr_parts = []
        attr_values = {}
        for k, v in updates.items():
            if k in allowed:
                expr_parts.append(f"{k} = :{k}")
                attr_values[f":{k}"] = v

        if not expr_parts:
            return {"error": "無可更新的欄位"}

        self.table.update_item(
            Key={_PK_FIELD: self._user_key(email)},
            UpdateExpression="SET " + ", ".join(expr_parts),
            ExpressionAttributeValues=attr_values,
        )
        return {"status": "ok"}

    def admin_delete_user(self, admin_email: str, target_email: str) -> dict:
        self._assert_admin(admin_email)

        if admin_email.lower() == target_email.lower():
            return {"error": "無法刪除自己"}

        self.table.delete_item(Key={_PK_FIELD: self._user_key(target_email)})
        return {"status": "ok"}
