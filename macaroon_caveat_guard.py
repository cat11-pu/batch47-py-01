"""Macaroon caveat authorization guard."""

from __future__ import annotations

import copy
import hashlib
import hmac
import json
import threading
from datetime import datetime, timezone
from typing import Any


class MacaroonError(ValueError):
    def __init__(self, code: str, detail: object = None) -> None:
        super().__init__(code)
        self.code = code
        self.detail = detail


class MacaroonCaveatGuard:
    """Issue macaroon credentials, attenuate them and authorize offline."""

    _VERSION = 1
    _TOKEN_FIELDS = (
        "version",
        "location",
        "identifier",
        "key_id",
        "caveats",
        "binding",
        "signature",
    )
    _CAVEAT_KINDS = {"scope", "context", "expires", "third_party"}
    _STAT_FIELDS = ("minted", "authorized", "denied", "attenuated")

    def __init__(self, *, clock_skew: int = 60, max_caveats: int = 16,
                 max_tokens: int = 500, clock=None) -> None:
        self.clock_skew = clock_skew
        self.max_caveats = max_caveats
        self.max_tokens = max_tokens
        self._clock = clock
        self._lock = threading.RLock()
        self._keys: dict[str, bytes] = {}
        self._revoked_keys: set[str] = set()
        self._ledger: dict[str, dict[str, Any]] = {}
        self._revoked: set[str] = set()
        self._stats = {name: 0 for name in self._STAT_FIELDS}

    @staticmethod
    def _fail(code: str, detail: object = None) -> None:
        raise MacaroonError(code, detail)

    @classmethod
    def _parse_time(cls, value: object) -> float:
        if isinstance(value, bool):
            raise ValueError("boolean is not a timestamp")
        if isinstance(value, datetime):
            if value.tzinfo is None:
                value = value.replace(tzinfo=timezone.utc)
            return value.timestamp()
        if isinstance(value, (int, float)):
            return float(value)
        if isinstance(value, str):
            text = value.strip()
            if text.endswith(("Z", "z")):
                text = text[:-1] + "+00:00"
            parsed = datetime.fromisoformat(text)
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            return parsed.timestamp()
        raise ValueError("unsupported timestamp")

    def _current_time(self, now: object = None) -> float:
        if now is not None:
            return self._parse_time(now)
        source = self._clock() if callable(self._clock) else self._clock
        if source is None:
            return datetime.now(timezone.utc).timestamp()
        return self._parse_time(source)

    @staticmethod
    def _canonical(value: object) -> bytes:
        return json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        ).encode("utf-8")

    def _signature(self, secret: bytes, *, location: str, identifier: str,
                   key_id: str, caveats: list[dict], binding: str | None) -> str:
        header = self._canonical([
            self._VERSION,
            location,
            identifier,
            key_id,
            binding,
        ])
        digest = hmac.new(secret, b"macaroon-caveat-v1\x00" + header,
                          hashlib.sha256).digest()
        for caveat in caveats:
            digest = hmac.new(
                secret,
                digest + b"\x00" + self._canonical(caveat),
                hashlib.sha256,
            ).digest()
        return digest.hex()

    def _resolve_key(self, key_id: str, *, allow_revoked: bool = False) -> bytes:
        if key_id not in self._keys:
            self._fail("MACAROON_UNKNOWN_KEY", key_id)
        if key_id in self._revoked_keys and not allow_revoked:
            self._fail("MACAROON_KEY_REVOKED", key_id)
        return self._keys[key_id]

    def _latest_key_id(self) -> str:
        for key_id in reversed(tuple(self._keys)):
            if key_id not in self._revoked_keys:
                return key_id
        self._fail("MACAROON_UNKNOWN_KEY", "no active root key")

    def _validate_token(self, token: object) -> dict:
        if not isinstance(token, dict):
            self._fail("MACAROON_INVALID_TOKEN", "token must be a mapping")
        for field in self._TOKEN_FIELDS:
            if field not in token:
                self._fail("MACAROON_INVALID_TOKEN", f"missing field: {field}")
        if token["version"] != self._VERSION:
            self._fail("MACAROON_INVALID_TOKEN", "unsupported version")
        if not isinstance(token["location"], str):
            self._fail("MACAROON_INVALID_TOKEN", "location must be a string")
        if not isinstance(token["identifier"], str):
            self._fail("MACAROON_INVALID_TOKEN", "identifier must be a string")
        if not isinstance(token["key_id"], str):
            self._fail("MACAROON_INVALID_TOKEN", "key_id must be a string")
        if not isinstance(token["caveats"], list):
            self._fail("MACAROON_INVALID_TOKEN", "caveats must be a list")
        if any(not isinstance(caveat, dict) for caveat in token["caveats"]):
            self._fail("MACAROON_INVALID_TOKEN", "caveats must be mappings")
        if token["binding"] is not None and not isinstance(token["binding"], str):
            self._fail("MACAROON_INVALID_TOKEN", "binding must be a string or null")
        if not isinstance(token["signature"], str):
            self._fail("MACAROON_INVALID_TOKEN", "signature must be a string")
        return token

    def _verify_token(self, token: object) -> tuple[dict, str, bytes]:
        checked = self._validate_token(token)
        secret = self._resolve_key(checked["key_id"])
        try:
            expected = self._signature(
                secret,
                location=checked["location"],
                identifier=checked["identifier"],
                key_id=checked["key_id"],
                caveats=checked["caveats"],
                binding=checked["binding"],
            )
        except (TypeError, ValueError, OverflowError) as exc:
            self._fail("MACAROON_SIGNATURE_INVALID", str(exc))
        if not hmac.compare_digest(expected, checked["signature"]):
            self._fail("MACAROON_SIGNATURE_INVALID", checked["identifier"])
        return checked, checked["identifier"], secret

    def _validate_caveat(self, caveat: object) -> dict:
        if not isinstance(caveat, dict):
            self._fail("MACAROON_CAVEAT_INVALID", "caveat must be a mapping")
        kind = caveat.get("kind")
        if kind not in self._CAVEAT_KINDS:
            self._fail("MACAROON_CAVEAT_INVALID", kind)

        normalized = copy.deepcopy(caveat)
        if kind == "scope":
            if "value" not in normalized or not isinstance(normalized["value"], str):
                self._fail("MACAROON_CAVEAT_INVALID", "scope needs a string value")
        elif kind == "context":
            if "key" not in normalized or not isinstance(normalized["key"], str):
                self._fail("MACAROON_CAVEAT_INVALID", "context needs a string key")
            if "value" not in normalized:
                self._fail("MACAROON_CAVEAT_INVALID", "context needs a value")
        elif kind == "expires":
            if "at" not in normalized:
                self._fail("MACAROON_CAVEAT_INVALID", "expires needs an at value")
            try:
                self._parse_time(normalized["at"])
            except (TypeError, ValueError, OverflowError):
                self._fail("MACAROON_CAVEAT_INVALID", normalized["at"])
            if isinstance(normalized["at"], datetime):
                normalized["at"] = normalized["at"].isoformat()
        else:
            if ("location" not in normalized
                    or not isinstance(normalized["location"], str)):
                self._fail("MACAROON_CAVEAT_INVALID",
                           "third_party needs a string location")
            if ("caveat_key" not in normalized
                    or not isinstance(normalized["caveat_key"], str)):
                self._fail("MACAROON_CAVEAT_INVALID",
                           "third_party needs a string caveat_key")

        try:
            self._canonical(normalized)
        except (TypeError, ValueError, OverflowError):
            self._fail("MACAROON_CAVEAT_INVALID", "caveat is not serializable")
        return normalized

    @staticmethod
    def _ledger_expiry(caveats: list[dict]) -> float | None:
        expiries: list[float] = []
        for caveat in caveats:
            if caveat.get("kind") == "expires":
                try:
                    expiries.append(MacaroonCaveatGuard._parse_time(caveat["at"]))
                except (KeyError, TypeError, ValueError, OverflowError):
                    continue
        return min(expiries) if expiries else None

    def _expired(self, expires_at: float | None, now: float) -> bool:
        return expires_at is not None and now >= expires_at + self.clock_skew

    @staticmethod
    def _context_scopes(context: dict) -> set:
        scopes = context.get("scopes")
        if scopes is None:
            return set()
        if isinstance(scopes, str):
            return {scopes}
        try:
            return set(scopes)
        except TypeError:
            return set()

    def _evaluate_caveats(self, caveats: list[dict], context: dict,
                          parent_signature: str, discharges: tuple,
                          now: float, seen: set[int]) -> None:
        scopes = self._context_scopes(context)
        for caveat in caveats:
            kind = caveat.get("kind")
            if kind == "scope":
                if caveat.get("value") not in scopes:
                    self._fail("MACAROON_SCOPE_MISSING", caveat.get("value"))
            elif kind == "context":
                key = caveat.get("key")
                if key not in context or context[key] != caveat.get("value"):
                    self._fail("MACAROON_CONTEXT_MISMATCH", key)
            elif kind == "expires":
                try:
                    expires_at = self._parse_time(caveat["at"])
                except (KeyError, TypeError, ValueError, OverflowError):
                    self._fail("MACAROON_CAVEAT_INVALID", caveat.get("at"))
                if now >= expires_at + self.clock_skew:
                    self._fail("MACAROON_EXPIRED", caveat.get("at"))
            elif kind == "third_party":
                caveat_key = caveat.get("caveat_key")
                candidates = [
                    discharge for discharge in discharges
                    if isinstance(discharge, dict)
                    and discharge.get("identifier") == caveat_key
                ]
                if not candidates:
                    self._fail("MACAROON_DISCHARGE_REQUIRED", caveat_key)
                for discharge in candidates:
                    try:
                        self._verify_discharge(
                            discharge,
                            parent_signature=parent_signature,
                            context=context,
                            discharges=discharges,
                            now=now,
                            seen=seen,
                        )
                    except MacaroonError:
                        continue
                    break
                else:
                    self._fail("MACAROON_DISCHARGE_INVALID", caveat_key)
            else:
                self._fail("MACAROON_CAVEAT_INVALID", kind)

    def _verify_discharge(self, discharge: dict, *, parent_signature: str,
                          context: dict, discharges: tuple, now: float,
                          seen: set[int]) -> None:
        marker = id(discharge)
        if marker in seen:
            self._fail("MACAROON_DISCHARGE_INVALID", "discharge cycle")
        try:
            checked, identifier, _ = self._verify_token(discharge)
        except MacaroonError as exc:
            self._fail("MACAROON_DISCHARGE_INVALID", exc.code)
        if checked["binding"] != parent_signature:
            self._fail("MACAROON_DISCHARGE_INVALID", "binding does not match parent")
        if identifier in self._revoked:
            self._fail("MACAROON_DISCHARGE_INVALID", "discharge revoked")
        self._evaluate_caveats(
            checked["caveats"],
            context,
            checked["signature"],
            discharges,
            now,
            seen | {marker},
        )

    def rotate_root_key(self, key_id: str, secret: bytes) -> dict:
        with self._lock:
            if not isinstance(key_id, str):
                self._fail("MACAROON_UNKNOWN_KEY", key_id)
            if not isinstance(secret, (bytes, bytearray, memoryview)):
                self._fail("MACAROON_INVALID_TOKEN", "secret must be bytes")
            material = bytes(secret)
            existing = self._keys.get(key_id)
            if existing is not None and existing != material:
                self._fail("MACAROON_KEY_CONFLICT", key_id)
            if existing is None:
                self._keys[key_id] = material
            return {
                "key_id": key_id,
                "revoked": key_id in self._revoked_keys,
            }

    def revoke_root_key(self, key_id: str) -> None:
        with self._lock:
            if key_id not in self._keys:
                self._fail("MACAROON_UNKNOWN_KEY", key_id)
            self._revoked_keys.add(key_id)

    def mint(self, *, location: str, identifier: str, caveats=(),
             key_id: str | None = None, now=None) -> dict:
        with self._lock:
            if not isinstance(location, str) or not isinstance(identifier, str):
                self._fail("MACAROON_INVALID_TOKEN", "location/identifier must be strings")
            if key_id is None:
                key_id = self._latest_key_id()
            elif not isinstance(key_id, str):
                self._fail("MACAROON_UNKNOWN_KEY", key_id)
            secret = self._resolve_key(key_id)

            try:
                raw_caveats = list(caveats)
            except TypeError:
                self._fail("MACAROON_CAVEAT_INVALID", "caveats must be iterable")
            normalized = [self._validate_caveat(caveat) for caveat in raw_caveats]
            if len(normalized) > self.max_caveats:
                self._fail("MACAROON_TOO_MANY_CAVEATS", len(normalized))

            if identifier in self._ledger:
                self._fail("MACAROON_DUPLICATE_IDENTIFIER", identifier)

            current = self._current_time(now)
            if len(self._ledger) >= self.max_tokens:
                self.purge(now=current)
                if len(self._ledger) >= self.max_tokens:
                    self._fail("MACAROON_LEDGER_FULL", identifier)

            signature = self._signature(
                secret,
                location=location,
                identifier=identifier,
                key_id=key_id,
                caveats=normalized,
                binding=None,
            )
            token = {
                "version": self._VERSION,
                "location": location,
                "identifier": identifier,
                "key_id": key_id,
                "caveats": normalized,
                "binding": None,
                "signature": signature,
            }
            self._ledger[identifier] = {
                "expires_at": self._ledger_expiry(normalized),
            }
            self._stats["minted"] += 1
            return copy.deepcopy(token)

    def attenuate(self, token: dict, caveat: dict) -> dict:
        with self._lock:
            checked, identifier, secret = self._verify_token(token)
            normalized = self._validate_caveat(caveat)
            if len(checked["caveats"]) + 1 > self.max_caveats:
                self._fail("MACAROON_TOO_MANY_CAVEATS",
                           len(checked["caveats"]) + 1)
            narrower = copy.deepcopy(checked)
            narrower["caveats"].append(normalized)
            narrower["signature"] = self._signature(
                secret,
                location=narrower["location"],
                identifier=identifier,
                key_id=narrower["key_id"],
                caveats=narrower["caveats"],
                binding=narrower["binding"],
            )
            self._stats["attenuated"] += 1
            return narrower

    def authorize(self, token: dict, *, context: dict | None = None,
                  discharges=(), now=None) -> dict:
        with self._lock:
            try:
                checked, identifier, _ = self._verify_token(token)
                if identifier in self._revoked:
                    self._fail("MACAROON_REVOKED", identifier)
                ctx = context if isinstance(context, dict) else {}
                if (checked["binding"] is not None
                        and ctx.get("binding") != checked["binding"]):
                    self._fail("MACAROON_BINDING_MISMATCH", checked["binding"])
                try:
                    discharge_list = tuple(discharges)
                except TypeError:
                    self._fail("MACAROON_DISCHARGE_INVALID", "discharges")
                current = self._current_time(now)
                self._evaluate_caveats(
                    checked["caveats"],
                    ctx,
                    checked["signature"],
                    discharge_list,
                    current,
                    set(),
                )
            except MacaroonError:
                self._stats["denied"] += 1
                raise
            self._stats["authorized"] += 1
            return {
                "status": "ok",
                "identifier": identifier,
                "key_id": checked["key_id"],
                "caveat_count": len(checked["caveats"]),
            }

    def bind(self, token: dict, *, binding: str) -> dict:
        with self._lock:
            checked, identifier, secret = self._verify_token(token)
            if checked["binding"] is not None:
                self._fail("MACAROON_ALREADY_BOUND", checked["binding"])
            if not isinstance(binding, str):
                self._fail("MACAROON_INVALID_TOKEN", "binding must be a string")
            bound = copy.deepcopy(checked)
            bound["binding"] = binding
            bound["signature"] = self._signature(
                secret,
                location=bound["location"],
                identifier=identifier,
                key_id=bound["key_id"],
                caveats=bound["caveats"],
                binding=binding,
            )
            return bound

    def inspect(self, token: dict) -> dict:
        with self._lock:
            checked, _, _ = self._verify_token(token)
            return {
                "identifier": checked["identifier"],
                "key_id": checked["key_id"],
                "location": checked["location"],
                "caveat_count": len(checked["caveats"]),
                "kinds": [caveat.get("kind") for caveat in checked["caveats"]],
                "caveats": copy.deepcopy(checked["caveats"]),
            }

    def revoke(self, identifier: str) -> None:
        with self._lock:
            self._revoked.add(identifier)

    def is_revoked(self, identifier: str) -> bool:
        with self._lock:
            return identifier in self._revoked

    def purge(self, now=None) -> int:
        with self._lock:
            current = self._current_time(now)
            expired = [
                identifier for identifier, entry in self._ledger.items()
                if self._expired(entry.get("expires_at"), current)
            ]
            for identifier in expired:
                del self._ledger[identifier]
            return len(expired)

    def snapshot(self) -> dict:
        with self._lock:
            return {
                "version": self._VERSION,
                "keys": {
                    key_id: secret.hex()
                    for key_id, secret in self._keys.items()
                },
                "revoked_keys": sorted(self._revoked_keys),
                "ledger": copy.deepcopy(self._ledger),
                "revoked": sorted(self._revoked),
                "stats": dict(self._stats),
            }

    def _restore_state(self, state: object) -> tuple[dict, set, dict, set, dict]:
        if not isinstance(state, dict):
            raise ValueError("snapshot must be a mapping")
        if isinstance(state.get("version"), bool) or state.get("version") != self._VERSION:
            raise ValueError("unsupported snapshot version")

        raw_keys = state.get("keys")
        if not isinstance(raw_keys, dict):
            raise ValueError("keys must be a mapping")
        keys: dict[str, bytes] = {}
        for key_id, material in raw_keys.items():
            if not isinstance(key_id, str):
                raise ValueError("key ids must be strings")
            if isinstance(material, str):
                try:
                    secret = bytes.fromhex(material)
                except ValueError as exc:
                    raise ValueError("invalid key material") from exc
            elif isinstance(material, (bytes, bytearray, memoryview)):
                secret = bytes(material)
            else:
                raise ValueError("invalid key material")
            keys[key_id] = secret

        raw_revoked_keys = state.get("revoked_keys", [])
        if not isinstance(raw_revoked_keys, list):
            raise ValueError("revoked_keys must be a list")
        revoked_keys: set[str] = set()
        for key_id in raw_revoked_keys:
            if not isinstance(key_id, str):
                raise ValueError("revoked key ids must be strings")
            revoked_keys.add(key_id)

        raw_ledger = state.get("ledger")
        if not isinstance(raw_ledger, dict):
            raise ValueError("ledger must be a mapping")
        ledger: dict[str, dict[str, Any]] = {}
        for identifier, entry in raw_ledger.items():
            if not isinstance(identifier, str) or not isinstance(entry, dict):
                raise ValueError("invalid ledger entry")
            expires_at = entry.get("expires_at")
            if expires_at is not None and (
                    isinstance(expires_at, bool)
                    or not isinstance(expires_at, (int, float, str, datetime))):
                raise ValueError("invalid ledger expiry")
            if expires_at is not None:
                try:
                    expires_at = self._parse_time(expires_at)
                except (TypeError, ValueError, OverflowError) as exc:
                    raise ValueError("invalid ledger expiry") from exc
            ledger[identifier] = {"expires_at": expires_at}

        raw_revoked = state.get("revoked")
        if not isinstance(raw_revoked, list):
            raise ValueError("revoked must be a list")
        revoked: set[str] = set()
        for identifier in raw_revoked:
            if not isinstance(identifier, str):
                raise ValueError("revoked identifiers must be strings")
            revoked.add(identifier)

        raw_stats = state.get("stats")
        if not isinstance(raw_stats, dict):
            raise ValueError("stats must be a mapping")
        if set(raw_stats) != set(self._STAT_FIELDS):
            raise ValueError("stats fields are invalid")
        stats: dict[str, int] = {}
        for name in self._STAT_FIELDS:
            value = raw_stats[name]
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ValueError("stats values must be non-negative integers")
            stats[name] = value
        return keys, revoked_keys, ledger, revoked, stats

    def restore(self, state: dict) -> None:
        with self._lock:
            try:
                keys, revoked_keys, ledger, revoked, stats = \
                    self._restore_state(state)
            except (TypeError, ValueError) as exc:
                self._fail("MACAROON_SNAPSHOT_INVALID", str(exc))
            self._keys = keys
            self._revoked_keys = revoked_keys
            self._ledger = ledger
            self._revoked = revoked
            self._stats = stats

    def stats(self) -> dict:
        with self._lock:
            return dict(self._stats)

    def reset_stats(self) -> None:
        with self._lock:
            self._stats = {name: 0 for name in self._STAT_FIELDS}
