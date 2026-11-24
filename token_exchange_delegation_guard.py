"""OAuth token exchange with delegated actor chains."""

from __future__ import annotations

import copy
import secrets
import threading
from datetime import datetime, timedelta, timezone

DEFAULT_MAX_DEPTH = 3
DEFAULT_TTL = 300
DEFAULT_MAX_CHILDREN = 8
DEFAULT_DEDUPE_WINDOW = 0
STAT_KEYS = ("issued", "exchanged", "deduped", "revoked", "rejected", "validated")
SNAPSHOT_VERSION = 1


class TokenExchangeError(ValueError):
    def __init__(self, code: str, detail: object = None) -> None:
        super().__init__(code)
        self.code = code
        self.detail = detail


def _is_name(value: object) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _normalize_names(value: object, *, allow_empty: bool = False) -> list[str]:
    if isinstance(value, str):
        values = [value]
    else:
        values = list(value)  # type: ignore[arg-type]
    if not values and not allow_empty:
        raise ValueError("collection must not be empty")
    names: list[str] = []
    seen: set[str] = set()
    for item in values:
        if not _is_name(item):
            raise ValueError("collection entries must be non-empty strings")
        if item not in seen:
            seen.add(item)
            names.append(item)
    return sorted(names)


def _to_datetime(value: object) -> datetime:
    if isinstance(value, datetime):
        result = value
    elif isinstance(value, (int, float)) and not isinstance(value, bool):
        result = datetime.fromtimestamp(value, tz=timezone.utc)
    elif isinstance(value, str):
        text = value.strip()
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        result = datetime.fromisoformat(text)
    else:
        raise ValueError("invalid timestamp")
    if result.tzinfo is None:
        result = result.replace(tzinfo=timezone.utc)
    return result


def _format_time(value: datetime) -> str:
    return value.isoformat()


class TokenExchangeGuard:
    """Issue root tokens and exchange them into narrowed delegated tokens."""

    def __init__(self, *, max_delegation_depth: int = DEFAULT_MAX_DEPTH,
                 default_ttl: int = DEFAULT_TTL,
                 max_children_per_token: int = DEFAULT_MAX_CHILDREN,
                 dedupe_window: int = DEFAULT_DEDUPE_WINDOW,
                 clock=None) -> None:
        self.max_delegation_depth = max_delegation_depth
        self.default_ttl = default_ttl
        self.max_children_per_token = max_children_per_token
        self.dedupe_window = dedupe_window
        self._clock = clock
        self._lock = threading.RLock()
        self._clients: dict[str, dict] = {}
        self._audiences: dict[str, dict] = {}
        self._tokens: dict[str, dict] = {}
        self._stats = {key: 0 for key in STAT_KEYS}

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _now(self, now) -> datetime:
        if now is None:
            if self._clock is None:
                now = datetime.now(timezone.utc)
            elif callable(self._clock):
                now = self._clock()
            else:
                now = self._clock
        return _to_datetime(now)

    def _fail(self, code: str, detail: object = None):
        self._stats["rejected"] += 1
        raise TokenExchangeError(code, detail)

    @staticmethod
    def _new_token() -> str:
        return "tx_" + secrets.token_urlsafe(32)

    @staticmethod
    def _client_view(client_id: str, config: dict) -> dict:
        return {
            "client_id": client_id,
            "allowed_audiences": list(config["allowed_audiences"]),
        }

    @staticmethod
    def _audience_view(audience: str, config: dict) -> dict:
        scopes = config["allowed_scopes"]
        return {
            "audience": audience,
            "max_delegation_depth": config["max_delegation_depth"],
            "allowed_scopes": None if scopes is None else list(scopes),
        }

    @staticmethod
    def _token_view(record: dict, *, deduped: bool | None = None) -> dict:
        view = {
            "token": record["token"],
            "subject": record["subject"],
            "scopes": list(record["scopes"]),
            "audience": record["audience"],
            "generation": record["generation"],
            "actor_chain": list(record["actor_chain"]),
            "parent": record["parent"],
            "expires_at": record["expires_at"],
            "issued_at": record["issued_at"],
            "client_id": record["client_id"],
            "revoked": record["revoked"],
        }
        if deduped is not None:
            view["deduped"] = deduped
        return view

    @staticmethod
    def _describe(record: dict, now: datetime) -> dict:
        if record["revoked"]:
            reason = "revoked"
        elif now >= _to_datetime(record["expires_at"]):
            reason = "expired"
        else:
            reason = None
        return {
            "active": reason is None,
            "token": record["token"],
            "subject": record["subject"],
            "scopes": list(record["scopes"]),
            "audience": record["audience"],
            "generation": record["generation"],
            "actor_chain": list(record["actor_chain"]),
            "parent": record["parent"],
            "expires_at": record["expires_at"],
            "issued_at": record["issued_at"],
            "client_id": record["client_id"],
            "revoked": record["revoked"],
            "revoked_reason": record["revoked_reason"],
            "reason": reason,
        }

    def _revoke_subtree(self, roots, reason: str) -> int:
        stack = list(roots)
        seen: set[str] = set()
        count = 0
        while stack:
            record = stack.pop()
            token = record["token"]
            if token in seen:
                continue
            seen.add(token)
            if not record["revoked"]:
                record["revoked"] = True
                record["revoked_reason"] = reason
                count += 1
            for child_token in record["children"]:
                child = self._tokens.get(child_token)
                if child is not None:
                    stack.append(child)
        self._stats["revoked"] += count
        return count

    # ------------------------------------------------------------------
    # Registration
    # ------------------------------------------------------------------

    def register_client(self, client_id: str, *, allowed_audiences) -> dict:
        with self._lock:
            try:
                if not _is_name(client_id):
                    raise ValueError("client_id must be a non-empty string")
                audiences = _normalize_names(allowed_audiences)
            except (TypeError, ValueError) as exc:
                self._fail("TX_CLIENT_CONFLICT", str(exc))

            config = {"allowed_audiences": audiences}
            existing = self._clients.get(client_id)
            if existing is not None:
                if existing == config:
                    return self._client_view(client_id, existing)
                self._fail("TX_CLIENT_CONFLICT", client_id)
            self._clients[client_id] = config
            return self._client_view(client_id, config)

    def register_audience(self, audience: str, *,
                          max_delegation_depth: int | None = None,
                          allowed_scopes=None) -> dict:
        with self._lock:
            try:
                if not _is_name(audience):
                    raise ValueError("audience must be a non-empty string")
                if max_delegation_depth is not None:
                    if isinstance(max_delegation_depth, bool) or \
                            not isinstance(max_delegation_depth, int) or \
                            max_delegation_depth < 0:
                        raise ValueError("max_delegation_depth must be >= 0 or None")
                if allowed_scopes is None:
                    scopes = None
                else:
                    scopes = _normalize_names(allowed_scopes)
            except (TypeError, ValueError) as exc:
                self._fail("TX_AUDIENCE_CONFLICT", str(exc))

            config = {
                "max_delegation_depth": max_delegation_depth,
                "allowed_scopes": scopes,
            }
            existing = self._audiences.get(audience)
            if existing is not None:
                if existing == config:
                    return self._audience_view(audience, existing)
                self._fail("TX_AUDIENCE_CONFLICT", audience)
            self._audiences[audience] = config
            return self._audience_view(audience, config)

    # ------------------------------------------------------------------
    # Issuance and exchange
    # ------------------------------------------------------------------

    def issue_root(self, subject: str, *, scopes, audience: str,
                   ttl: int | None = None, now=None) -> dict:
        with self._lock:
            try:
                if not _is_name(subject):
                    raise ValueError("subject must be a non-empty string")
                normalized_scopes = _normalize_names(scopes)
                if not normalized_scopes:
                    raise ValueError("scopes must not be empty")
                issued_at = self._now(now)
                if ttl is None:
                    ttl = self.default_ttl
                if isinstance(ttl, bool) or not isinstance(ttl, (int, float)):
                    raise ValueError("ttl must be a number")
            except (TypeError, ValueError) as exc:
                self._fail("TX_SCOPE_ESCALATION", str(exc))

            audience_config = self._audiences.get(audience) if _is_name(audience) else None
            if audience_config is None:
                self._fail("TX_UNKNOWN_AUDIENCE", audience)
            allowed_scopes = audience_config["allowed_scopes"]
            if allowed_scopes is not None and \
                    not set(normalized_scopes).issubset(set(allowed_scopes)):
                self._fail("TX_SCOPE_NOT_ALLOWED", normalized_scopes)

            expires_at = issued_at + timedelta(seconds=ttl)
            token = self._new_token()
            while token in self._tokens:
                token = self._new_token()
            record = {
                "token": token,
                "subject": subject,
                "scopes": normalized_scopes,
                "audience": audience,
                "generation": 0,
                "actor_chain": [],
                "parent": None,
                "children": [],
                "expires_at": _format_time(expires_at),
                "issued_at": _format_time(issued_at),
                "client_id": None,
                "actor": None,
                "revoked": False,
                "revoked_reason": None,
            }
            self._tokens[token] = record
            self._stats["issued"] += 1
            return self._token_view(record)

    def exchange(self, subject_token: str, *, client_id: str, audience: str,
                 scopes=None, actor_token=None, now=None) -> dict:
        with self._lock:
            parent = self._tokens.get(subject_token) if _is_name(subject_token) else None
            if parent is None:
                self._fail("TX_UNKNOWN_TOKEN", subject_token)

            client = self._clients.get(client_id) if _is_name(client_id) else None
            if client is None:
                self._fail("TX_UNKNOWN_CLIENT", client_id)

            audience_config = self._audiences.get(audience) if _is_name(audience) else None
            if audience_config is None:
                self._fail("TX_UNKNOWN_AUDIENCE", audience)

            if audience not in client["allowed_audiences"]:
                self._fail("TX_AUDIENCE_NOT_ALLOWED", audience)

            try:
                current = self._now(now)
            except (TypeError, ValueError) as exc:
                self._fail("TX_TOKEN_EXPIRED", str(exc))

            if parent["revoked"]:
                self._fail("TX_TOKEN_REVOKED", subject_token)
            if current >= _to_datetime(parent["expires_at"]):
                self._fail("TX_TOKEN_EXPIRED", subject_token)

            if scopes is None:
                requested_scopes = list(parent["scopes"])
            else:
                try:
                    requested_scopes = _normalize_names(scopes)
                except (TypeError, ValueError) as exc:
                    self._fail("TX_SCOPE_ESCALATION", str(exc))
                if not requested_scopes:
                    self._fail("TX_SCOPE_ESCALATION", "scopes must not be empty")
                if not set(requested_scopes).issubset(set(parent["scopes"])):
                    self._fail("TX_SCOPE_ESCALATION", requested_scopes)

            allowed_scopes = audience_config["allowed_scopes"]
            if allowed_scopes is not None and \
                    not set(requested_scopes).issubset(set(allowed_scopes)):
                self._fail("TX_SCOPE_NOT_ALLOWED", requested_scopes)

            actor_subject = None
            if actor_token is not None:
                actor = self._tokens.get(actor_token) if _is_name(actor_token) else None
                if actor is None:
                    self._fail("TX_ACTOR_INVALID", actor_token)
                if actor["revoked"] or current >= _to_datetime(actor["expires_at"]):
                    self._fail("TX_ACTOR_INVALID", actor_token)
                if actor["subject"] == parent["subject"]:
                    self._fail("TX_ACTOR_SELF", actor_token)
                actor_subject = actor["subject"]

            # A replay inside the dedupe window is resolved before the depth
            # and fan-out limits, because it does not create another token.
            if self.dedupe_window > 0:
                for child_token in parent["children"]:
                    child = self._tokens.get(child_token)
                    if child is None or child["revoked"]:
                        continue
                    if current >= _to_datetime(child["expires_at"]):
                        continue
                    if child["client_id"] != client_id:
                        continue
                    if child["audience"] != audience:
                        continue
                    if child["scopes"] != requested_scopes:
                        continue
                    if child["actor"] != actor_subject:
                        continue
                    age = (current - _to_datetime(child["issued_at"])).total_seconds()
                    if abs(age) <= self.dedupe_window:
                        self._stats["deduped"] += 1
                        return self._token_view(child, deduped=True)

            generation = parent["generation"] + 1
            effective_depth = self.max_delegation_depth
            audience_depth = audience_config["max_delegation_depth"]
            if audience_depth is not None:
                effective_depth = min(effective_depth, audience_depth)
            if generation > effective_depth:
                self._fail("TX_DEPTH_EXCEEDED", generation)

            if len(parent["children"]) >= self.max_children_per_token:
                self._fail("TX_FANOUT_EXCEEDED", len(parent["children"]))

            actor_chain = list(parent["actor_chain"])
            if actor_subject is not None:
                actor_chain.append(actor_subject)

            expires_at = min(
                current + timedelta(seconds=self.default_ttl),
                _to_datetime(parent["expires_at"]),
            )
            token = self._new_token()
            while token in self._tokens:
                token = self._new_token()
            record = {
                "token": token,
                "subject": parent["subject"],
                "scopes": requested_scopes,
                "audience": audience,
                "generation": generation,
                "actor_chain": actor_chain,
                "parent": parent["token"],
                "children": [],
                "expires_at": _format_time(expires_at),
                "issued_at": _format_time(current),
                "client_id": client_id,
                "actor": actor_subject,
                "revoked": False,
                "revoked_reason": None,
            }
            self._tokens[token] = record
            parent["children"].append(token)
            self._stats["exchanged"] += 1
            return self._token_view(record, deduped=False)

    # ------------------------------------------------------------------
    # Validation and inspection
    # ------------------------------------------------------------------

    def validate(self, token: str, *, audience: str,
                 required_scope: str | None = None, now=None) -> dict:
        with self._lock:
            record = self._tokens.get(token) if _is_name(token) else None
            if record is None:
                self._fail("TX_UNKNOWN_TOKEN", token)
            try:
                current = self._now(now)
            except (TypeError, ValueError) as exc:
                self._fail("TX_TOKEN_EXPIRED", str(exc))
            if record["revoked"]:
                self._fail("TX_TOKEN_REVOKED", token)
            if current >= _to_datetime(record["expires_at"]):
                self._fail("TX_TOKEN_EXPIRED", token)
            if record["audience"] != audience:
                self._fail("TX_AUDIENCE_MISMATCH", audience)
            if required_scope is not None and required_scope not in record["scopes"]:
                self._fail("TX_SCOPE_MISSING", required_scope)
            self._stats["validated"] += 1
            return self._describe(record, current)

    def introspect(self, token: str, *, now=None) -> dict:
        with self._lock:
            record = self._tokens.get(token) if _is_name(token) else None
            if record is None:
                self._fail("TX_UNKNOWN_TOKEN", token)
            try:
                current = self._now(now)
            except (TypeError, ValueError) as exc:
                self._fail("TX_TOKEN_EXPIRED", str(exc))
            return self._describe(record, current)

    def chain(self, token: str) -> list:
        with self._lock:
            record = self._tokens.get(token) if _is_name(token) else None
            if record is None:
                self._fail("TX_UNKNOWN_TOKEN", token)
            return [record["subject"], *record["actor_chain"]]

    # ------------------------------------------------------------------
    # Revocation and cleanup
    # ------------------------------------------------------------------

    def revoke(self, token: str, *, reason: str = "manual") -> int:
        with self._lock:
            record = self._tokens.get(token) if _is_name(token) else None
            if record is None:
                self._fail("TX_UNKNOWN_TOKEN", token)
            return self._revoke_subtree([record], reason)

    def revoke_principal(self, principal: str, *,
                         reason: str = "offboarded") -> int:
        with self._lock:
            roots = [
                record for record in self._tokens.values()
                if record["subject"] == principal or principal in record["actor_chain"]
            ]
            if not roots:
                return 0
            return self._revoke_subtree(roots, reason)

    def purge(self, now=None) -> int:
        with self._lock:
            try:
                current = self._now(now)
            except (TypeError, ValueError):
                return 0
            expired = [
                token for token, record in self._tokens.items()
                if current >= _to_datetime(record["expires_at"])
            ]
            expired_set = set(expired)
            for token in expired:
                self._tokens.pop(token, None)
            for record in self._tokens.values():
                if record["children"]:
                    record["children"] = [
                        child for child in record["children"]
                        if child not in expired_set and child in self._tokens
                    ]
            return len(expired)

    # ------------------------------------------------------------------
    # Persistence and statistics
    # ------------------------------------------------------------------

    def snapshot(self) -> dict:
        with self._lock:
            state = {
                "version": SNAPSHOT_VERSION,
                "config": {
                    "max_delegation_depth": self.max_delegation_depth,
                    "default_ttl": self.default_ttl,
                    "max_children_per_token": self.max_children_per_token,
                    "dedupe_window": self.dedupe_window,
                },
                "clients": copy.deepcopy(self._clients),
                "audiences": copy.deepcopy(self._audiences),
                "tokens": copy.deepcopy(self._tokens),
                "stats": dict(self._stats),
            }
            return copy.deepcopy(state)

    def _prepare_restore(self, state: object) -> dict:
        if not isinstance(state, dict):
            raise ValueError("snapshot must be an object")
        if state.get("version") != SNAPSHOT_VERSION:
            raise ValueError("unsupported snapshot version")
        for key in ("config", "clients", "audiences", "tokens", "stats"):
            if key not in state:
                raise ValueError(f"missing snapshot section: {key}")

        raw_config = state["config"]
        if not isinstance(raw_config, dict):
            raise ValueError("config must be an object")
        config = {}
        for key in ("max_delegation_depth", "default_ttl",
                    "max_children_per_token", "dedupe_window"):
            value = raw_config.get(key)
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ValueError(f"invalid config value: {key}")
            config[key] = value

        raw_clients = state["clients"]
        if not isinstance(raw_clients, dict):
            raise ValueError("clients must be an object")
        clients: dict[str, dict] = {}
        for client_id, raw in raw_clients.items():
            if not _is_name(client_id) or not isinstance(raw, dict):
                raise ValueError("invalid client entry")
            if "allowed_audiences" not in raw:
                raise ValueError("client is missing allowed_audiences")
            try:
                audiences = _normalize_names(raw["allowed_audiences"])
            except (TypeError, ValueError) as exc:
                raise ValueError("invalid client audiences") from exc
            clients[client_id] = {"allowed_audiences": audiences}

        raw_audiences = state["audiences"]
        if not isinstance(raw_audiences, dict):
            raise ValueError("audiences must be an object")
        audiences: dict[str, dict] = {}
        for audience, raw in raw_audiences.items():
            if not _is_name(audience) or not isinstance(raw, dict):
                raise ValueError("invalid audience entry")
            depth = raw.get("max_delegation_depth")
            if depth is not None and (
                    isinstance(depth, bool) or not isinstance(depth, int) or depth < 0):
                raise ValueError("invalid audience depth")
            raw_scopes = raw.get("allowed_scopes")
            if raw_scopes is None:
                scopes = None
            else:
                try:
                    scopes = _normalize_names(raw_scopes)
                except (TypeError, ValueError) as exc:
                    raise ValueError("invalid audience scopes") from exc
            audiences[audience] = {
                "max_delegation_depth": depth,
                "allowed_scopes": scopes,
            }

        raw_stats = state["stats"]
        if not isinstance(raw_stats, dict) or set(raw_stats) != set(STAT_KEYS):
            raise ValueError("invalid stats")
        stats: dict[str, int] = {}
        for key in STAT_KEYS:
            value = raw_stats[key]
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ValueError("invalid stat value")
            stats[key] = value

        raw_tokens = state["tokens"]
        if not isinstance(raw_tokens, dict):
            raise ValueError("tokens must be an object")
        tokens: dict[str, dict] = {}
        for token, raw in raw_tokens.items():
            if not _is_name(token) or not isinstance(raw, dict):
                raise ValueError("invalid token entry")
            if raw.get("token") != token:
                raise ValueError("token key does not match record")
            subject = raw.get("subject")
            if not _is_name(subject):
                raise ValueError("invalid token subject")
            audience = raw.get("audience")
            if not _is_name(audience) or audience not in audiences:
                raise ValueError("invalid token audience")
            try:
                scopes = _normalize_names(raw.get("scopes"))
            except (TypeError, ValueError) as exc:
                raise ValueError("invalid token scopes") from exc
            generation = raw.get("generation")
            if isinstance(generation, bool) or not isinstance(generation, int) or generation < 0:
                raise ValueError("invalid token generation")
            raw_chain = raw.get("actor_chain")
            if not isinstance(raw_chain, list):
                raise ValueError("invalid actor chain")
            actor_chain = []
            for actor in raw_chain:
                if not _is_name(actor):
                    raise ValueError("invalid actor chain entry")
                actor_chain.append(actor)
            parent = raw.get("parent")
            if parent is not None and not _is_name(parent):
                raise ValueError("invalid token parent")
            raw_children = raw.get("children")
            if not isinstance(raw_children, list):
                raise ValueError("invalid token children")
            children = []
            seen_children: set[str] = set()
            for child in raw_children:
                if not _is_name(child) or child in seen_children:
                    raise ValueError("invalid token child")
                seen_children.add(child)
                children.append(child)
            try:
                expires_at = _to_datetime(raw.get("expires_at"))
                issued_at = _to_datetime(raw.get("issued_at"))
            except (TypeError, ValueError) as exc:
                raise ValueError("invalid token timestamps") from exc
            revoked = raw.get("revoked")
            if not isinstance(revoked, bool):
                raise ValueError("invalid revoked flag")
            revoked_reason = raw.get("revoked_reason")
            if revoked_reason is not None and not isinstance(revoked_reason, str):
                raise ValueError("invalid revocation reason")
            client_id = raw.get("client_id")
            if client_id is not None and client_id not in clients:
                raise ValueError("invalid token client")
            actor = raw.get("actor")
            if actor is not None and not _is_name(actor):
                raise ValueError("invalid token actor")
            tokens[token] = {
                "token": token,
                "subject": subject,
                "scopes": scopes,
                "audience": audience,
                "generation": generation,
                "actor_chain": actor_chain,
                "parent": parent,
                "children": children,
                "expires_at": _format_time(expires_at),
                "issued_at": _format_time(issued_at),
                "client_id": client_id,
                "actor": actor,
                "revoked": revoked,
                "revoked_reason": revoked_reason,
            }

        for token, record in tokens.items():
            parent_token = record["parent"]
            audience_config = audiences[record["audience"]]
            allowed_scopes = audience_config["allowed_scopes"]
            if allowed_scopes is not None and \
                    not set(record["scopes"]).issubset(set(allowed_scopes)):
                raise ValueError("token scopes exceed audience allowance")
            effective_depth = config["max_delegation_depth"]
            audience_depth = audience_config["max_delegation_depth"]
            if audience_depth is not None:
                effective_depth = min(effective_depth, audience_depth)
            if record["generation"] > effective_depth:
                raise ValueError("token exceeds delegation depth")
            if len(record["children"]) > config["max_children_per_token"]:
                raise ValueError("token exceeds fan-out limit")
            if parent_token is None:
                if record["generation"] != 0 or record["actor_chain"]:
                    raise ValueError("invalid root token")
                if record["client_id"] is not None or record["actor"] is not None:
                    raise ValueError("invalid root token metadata")
                continue
            parent = tokens.get(parent_token)
            if parent is None:
                raise ValueError("orphan token")
            if record["generation"] != parent["generation"] + 1:
                raise ValueError("invalid token generation")
            expected_chain = list(parent["actor_chain"])
            if record["actor"] is not None:
                expected_chain.append(record["actor"])
            if record["actor_chain"] != expected_chain:
                raise ValueError("invalid actor chain")
            if record["subject"] != parent["subject"]:
                raise ValueError("subject changed during delegation")
            if token not in parent["children"]:
                raise ValueError("parent is missing child")
            if not set(record["scopes"]).issubset(set(parent["scopes"])):
                raise ValueError("scope widened during delegation")
            if _to_datetime(record["expires_at"]) > _to_datetime(parent["expires_at"]):
                raise ValueError("child outlives parent")
            if record["client_id"] is None:
                raise ValueError("delegated token is missing client")
            client = clients[record["client_id"]]
            if record["audience"] not in client["allowed_audiences"]:
                raise ValueError("client is not allowed for token audience")

        for token, record in tokens.items():
            for child_token in record["children"]:
                child = tokens.get(child_token)
                if child is None or child["parent"] != token:
                    raise ValueError("inconsistent parent/child link")
            seen: set[str] = set()
            cursor = record
            while cursor is not None:
                if cursor["token"] in seen:
                    raise ValueError("token cycle")
                seen.add(cursor["token"])
                parent_token = cursor["parent"]
                cursor = None if parent_token is None else tokens.get(parent_token)

        return {
            "config": config,
            "clients": clients,
            "audiences": audiences,
            "tokens": tokens,
            "stats": stats,
        }

    def restore(self, state: dict) -> None:
        with self._lock:
            try:
                prepared = self._prepare_restore(state)
            except Exception as exc:
                raise TokenExchangeError("TX_SNAPSHOT_INVALID", str(exc)) from exc

            config = prepared["config"]
            self.max_delegation_depth = config["max_delegation_depth"]
            self.default_ttl = config["default_ttl"]
            self.max_children_per_token = config["max_children_per_token"]
            self.dedupe_window = config["dedupe_window"]
            self._clients = prepared["clients"]
            self._audiences = prepared["audiences"]
            self._tokens = prepared["tokens"]
            self._stats = prepared["stats"]

    def stats(self) -> dict:
        with self._lock:
            return {key: self._stats[key] for key in STAT_KEYS}

    def reset_stats(self) -> None:
        with self._lock:
            self._stats = {key: 0 for key in STAT_KEYS}
