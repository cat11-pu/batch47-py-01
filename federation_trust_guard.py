"""OpenID Federation trust chain resolution and verification."""

from __future__ import annotations

import copy
import heapq
import threading
from datetime import date, datetime, timezone

DEFAULT_MAX_CHAIN_LENGTH = 5
STAT_KEYS = ("resolved", "verified", "revoked", "rejected")
SNAPSHOT_VERSION = 1


class FederationError(ValueError):
    def __init__(self, code: str, detail: object = None) -> None:
        super().__init__(code)
        self.code = code
        self.detail = detail


def _is_nonempty_string(value: object) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _parse_instant(value: object) -> datetime:
    """Return an aware UTC datetime for the values accepted by this module."""
    if isinstance(value, datetime):
        moment = value
    elif isinstance(value, date):
        moment = datetime(value.year, value.month, value.day)
    elif isinstance(value, (int, float)) and not isinstance(value, bool):
        return datetime.fromtimestamp(value, timezone.utc)
    elif isinstance(value, str):
        text = value.strip()
        if not text:
            raise ValueError("empty timestamp")
        if text.endswith(("Z", "z")):
            text = text[:-1] + "+00:00"
        moment = datetime.fromisoformat(text)
    else:
        raise ValueError("unsupported timestamp")

    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(timezone.utc)


class FederationTrustGuard:
    """Store anchors, entity configurations and subordinate statements."""

    def __init__(self, *, max_chain_length: int = DEFAULT_MAX_CHAIN_LENGTH,
                 clock=None) -> None:
        if (not isinstance(max_chain_length, int)
                or isinstance(max_chain_length, bool)
                or max_chain_length < 0):
            raise ValueError("max_chain_length must be a non-negative integer")
        self.max_chain_length = max_chain_length
        self._clock = clock
        self._lock = threading.RLock()

        self._anchors: dict[str, dict] = {}
        self._configs: dict[str, dict] = {}
        self._statements: dict[tuple[str, str], dict] = {}
        self._revoked: dict[str, str] = {}
        self._stats = {key: 0 for key in STAT_KEYS}

    # ------------------------------------------------------------------
    # Time and validation helpers
    # ------------------------------------------------------------------
    def _now(self, now=None) -> datetime:
        value = now
        if value is None:
            if self._clock is not None:
                value = self._clock()
            else:
                value = datetime.now(timezone.utc)
        return _parse_instant(value)

    @staticmethod
    def _expired(record: dict, now: datetime) -> bool:
        return _parse_instant(record["expires_at"]) <= now

    @staticmethod
    def _normalize_hints(authority_hints):
        if authority_hints is None:
            return None
        if isinstance(authority_hints, str):
            items = [authority_hints]
        else:
            try:
                items = list(authority_hints)
            except TypeError as exc:
                raise FederationError("FED_INVALID_CONFIG",
                                      "authority_hints must be a collection") from exc
        if not items:
            raise FederationError("FED_INVALID_CONFIG",
                                  "authority_hints cannot be empty")
        if any(not _is_nonempty_string(item) for item in items):
            raise FederationError("FED_INVALID_CONFIG",
                                  "authority_hints must contain entity ids")
        return sorted(set(items))

    def _known_entities_locked(self) -> set[str]:
        known = set(self._anchors)
        known.update(self._configs)
        known.update(self._revoked)
        for statement in self._statements.values():
            known.add(statement["issuer"])
            known.add(statement["subject"])
        return known

    def _entity_fingerprint_locked(self, entity_id: str):
        config = self._configs.get(entity_id)
        if config is not None:
            return config["jwks_fingerprint"]
        anchor = self._anchors.get(entity_id)
        if anchor is not None:
            return anchor["jwks_fingerprint"]
        return None

    def _hints_allow_locked(self, issuer: str, subject: str) -> bool:
        config = self._configs.get(subject)
        if config is None or config["authority_hints"] is None:
            return True
        return issuer in config["authority_hints"]

    def _outgoing_locked(self) -> dict[str, list[dict]]:
        outgoing: dict[str, list[dict]] = {}
        for statement in self._statements.values():
            outgoing.setdefault(statement["issuer"], []).append(statement)
        for statements in outgoing.values():
            statements.sort(key=lambda item: item["subject"])
        return outgoing

    # ------------------------------------------------------------------
    # Path search
    # ------------------------------------------------------------------
    def _shortest_path_locked(self, target: str, now: datetime, *,
                              ignore_expiry: bool = False,
                              ignore_hints: bool = False):
        """Find the deterministic shortest path to *target*.

        Revoked entities are never part of a path.  Expiry and authority hints
        can be disabled for diagnostics; path ordering itself is always by
        (edge count, complete path).
        """
        if target in self._anchors:
            if target in self._revoked:
                return None
            if not ignore_expiry and self._expired(
                    self._anchors[target], now):
                return None
            target_config = self._configs.get(target)
            if (not ignore_expiry and target_config is not None
                    and self._expired(target_config, now)):
                return None
            return [target]

        if not ignore_expiry:
            target_config = self._configs.get(target)
            if target_config is not None and self._expired(target_config, now):
                return None

        outgoing = self._outgoing_locked()
        heap: list[tuple[int, tuple[str, ...]]] = []
        for anchor_id in sorted(self._anchors):
            if anchor_id in self._revoked:
                continue
            if not ignore_expiry and self._expired(
                    self._anchors[anchor_id], now):
                continue
            heapq.heappush(heap, (0, (anchor_id,)))

        visited: set[str] = set()
        while heap:
            distance, path = heapq.heappop(heap)
            node = path[-1]
            if node in visited:
                continue
            visited.add(node)
            if node == target:
                return list(path)

            for statement in outgoing.get(node, ()):
                subject = statement["subject"]
                if subject in visited or subject in path:
                    continue
                if subject in self._revoked:
                    continue
                if not ignore_expiry and self._expired(statement, now):
                    continue
                if (not ignore_hints
                        and not self._hints_allow_locked(node, subject)):
                    continue
                heapq.heappush(heap, (distance + 1, path + (subject,)))
        return None

    def _resolve_locked(self, entity_id: str, now: datetime) -> list:
        known = self._known_entities_locked()
        if entity_id not in known:
            raise FederationError("FED_UNKNOWN_ENTITY", entity_id)
        if entity_id in self._revoked:
            raise FederationError("FED_REVOKED", entity_id)
        if not self._anchors:
            raise FederationError("FED_NO_TRUST_ANCHOR", entity_id)

        structural = self._shortest_path_locked(
            entity_id, now, ignore_expiry=True, ignore_hints=True)
        if structural is None:
            raise FederationError("FED_NO_TRUST_PATH", entity_id)

        structural_length = len(structural) - 1
        if structural_length > self.max_chain_length:
            raise FederationError("FED_CHAIN_TOO_LONG", structural_length)

        valid = self._shortest_path_locked(entity_id, now)
        if valid is not None:
            if len(valid) - 1 > self.max_chain_length:
                raise FederationError("FED_CHAIN_TOO_LONG", len(valid) - 1)
            return valid

        # No valid path exists.  Distinguish the reason using progressively
        # relaxed searches so the most specific error is reported.
        if self._shortest_path_locked(
                entity_id, now, ignore_expiry=True) is not None:
            raise FederationError("FED_EXPIRED", entity_id)
        if self._shortest_path_locked(
                entity_id, now, ignore_hints=True) is not None:
            raise FederationError("FED_AUTHORITY_MISMATCH", entity_id)

        # A path may violate both expiry and hints.  In that case inspect the
        # deterministic structural path to report the first applicable reason.
        for index in range(len(structural) - 1):
            issuer, subject = structural[index], structural[index + 1]
            statement = self._statements.get((issuer, subject))
            if statement is not None and self._expired(statement, now):
                raise FederationError("FED_EXPIRED", entity_id)
            if not self._hints_allow_locked(issuer, subject):
                raise FederationError("FED_AUTHORITY_MISMATCH", entity_id)
        anchor = self._anchors.get(structural[0])
        if anchor is not None and self._expired(anchor, now):
            raise FederationError("FED_EXPIRED", entity_id)
        target_config = self._configs.get(entity_id)
        if target_config is not None and self._expired(target_config, now):
            raise FederationError("FED_EXPIRED", entity_id)

        raise FederationError("FED_NO_TRUST_PATH", entity_id)

    # ------------------------------------------------------------------
    # Registration
    # ------------------------------------------------------------------
    def add_trust_anchor(self, entity_id: str, *, jwks_fingerprint: str,
                         expires_at: str, now=None) -> dict:
        with self._lock:
            if not _is_nonempty_string(entity_id) or not _is_nonempty_string(
                    jwks_fingerprint):
                raise FederationError(
                    "FED_INVALID_CONFIG",
                    "entity_id and jwks_fingerprint are required")
            try:
                expires = _parse_instant(expires_at)
            except Exception as exc:
                raise FederationError("FED_INVALID_CONFIG",
                                      "invalid expires_at") from exc
            self._now(now)

            existing = self._anchors.get(entity_id)
            record = {
                "entity_id": entity_id,
                "jwks_fingerprint": jwks_fingerprint,
                "expires_at": expires_at,
            }
            if existing is not None:
                if (existing["jwks_fingerprint"] != jwks_fingerprint
                        or _parse_instant(existing["expires_at"]) != expires):
                    raise FederationError("FED_CONFLICT", entity_id)
                return copy.deepcopy(existing)
            self._anchors[entity_id] = record
            return copy.deepcopy(record)

    def add_entity_configuration(self, entity_id: str, *, issuer: str,
                                 jwks_fingerprint: str, authority_hints=None,
                                 metadata=None, expires_at: str,
                                 now=None) -> dict:
        with self._lock:
            if (not _is_nonempty_string(entity_id)
                    or not _is_nonempty_string(jwks_fingerprint)
                    or issuer != entity_id):
                raise FederationError("FED_INVALID_CONFIG", entity_id)
            hints = self._normalize_hints(authority_hints)
            try:
                expires = _parse_instant(expires_at)
            except Exception as exc:
                raise FederationError("FED_INVALID_CONFIG",
                                      "invalid expires_at") from exc
            self._now(now)

            record = {
                "entity_id": entity_id,
                "issuer": issuer,
                "jwks_fingerprint": jwks_fingerprint,
                "authority_hints": hints,
                "metadata": copy.deepcopy(metadata),
                "expires_at": expires_at,
            }
            existing = self._configs.get(entity_id)
            if existing is not None:
                if (existing["issuer"] != issuer
                        or existing["jwks_fingerprint"] != jwks_fingerprint
                        or existing["authority_hints"] != hints
                        or existing["metadata"] != metadata
                        or _parse_instant(existing["expires_at"]) != expires):
                    raise FederationError("FED_CONFLICT", entity_id)
                return copy.deepcopy(existing)
            self._configs[entity_id] = record
            return copy.deepcopy(record)

    def add_subordinate_statement(self, issuer: str, *, subject: str,
                                  jwks_fingerprint: str, metadata=None,
                                  expires_at: str, now=None) -> dict:
        with self._lock:
            if issuer not in self._anchors and issuer not in self._configs:
                raise FederationError("FED_UNKNOWN_ENTITY", issuer)
            if (not _is_nonempty_string(subject)
                    or not _is_nonempty_string(jwks_fingerprint)
                    or issuer == subject):
                raise FederationError(
                    "FED_INVALID_STATEMENT",
                    "subject and jwks_fingerprint are required")
            try:
                expires = _parse_instant(expires_at)
            except Exception as exc:
                raise FederationError("FED_INVALID_STATEMENT",
                                      "invalid expires_at") from exc
            self._now(now)

            key = (issuer, subject)
            record = {
                "issuer": issuer,
                "subject": subject,
                "jwks_fingerprint": jwks_fingerprint,
                "metadata": copy.deepcopy(metadata),
                "expires_at": expires_at,
            }
            existing = self._statements.get(key)
            if existing is not None:
                if (existing["jwks_fingerprint"] != jwks_fingerprint
                        or existing["metadata"] != metadata
                        or _parse_instant(existing["expires_at"]) != expires):
                    raise FederationError("FED_CONFLICT", key)
                return copy.deepcopy(existing)
            self._statements[key] = record
            return copy.deepcopy(record)

    # ------------------------------------------------------------------
    # Resolution and verification
    # ------------------------------------------------------------------
    def resolve_chain(self, entity_id: str, *, now=None) -> list:
        with self._lock:
            try:
                now_dt = self._now(now)
                chain = self._resolve_locked(entity_id, now_dt)
            except FederationError:
                self._stats["rejected"] += 1
                raise
            self._stats["resolved"] += 1
            return chain

    def verify_entity(self, entity_id: str, *, expected_fingerprint: str,
                      now=None) -> dict:
        with self._lock:
            try:
                now_dt = self._now(now)
                chain = self._resolve_locked(entity_id, now_dt)

                actual = self._entity_fingerprint_locked(entity_id)
                if actual is None:
                    statement = self._statements.get((chain[-2], entity_id)) \
                        if len(chain) > 1 else None
                    actual = (statement["jwks_fingerprint"]
                              if statement is not None else None)
                if actual != expected_fingerprint:
                    raise FederationError("FED_FINGERPRINT_MISMATCH",
                                          entity_id)

                for index in range(len(chain) - 1):
                    issuer, subject = chain[index], chain[index + 1]
                    statement = self._statements.get((issuer, subject))
                    subject_fp = self._entity_fingerprint_locked(subject)
                    if (statement is not None and subject_fp is not None
                            and statement["jwks_fingerprint"] != subject_fp):
                        raise FederationError("FED_FINGERPRINT_MISMATCH",
                                              entity_id)

                expires_at = None
                config = self._configs.get(entity_id)
                anchor = self._anchors.get(entity_id)
                if config is not None:
                    expires_at = config["expires_at"]
                elif anchor is not None:
                    expires_at = anchor["expires_at"]
                elif len(chain) > 1:
                    statement = self._statements.get((chain[-2], entity_id))
                    if statement is not None:
                        expires_at = statement["expires_at"]
            except FederationError:
                self._stats["rejected"] += 1
                raise

            self._stats["verified"] += 1
            return {
                "entity_id": entity_id,
                "trust_anchor": chain[0],
                "chain": chain,
                "fingerprint": actual,
                "expires_at": expires_at,
            }

    def trusted_entities(self, now=None) -> list:
        with self._lock:
            now_dt = self._now(now)
            trusted = []
            for entity_id in sorted(self._known_entities_locked()):
                if entity_id in self._revoked:
                    continue
                path = self._shortest_path_locked(entity_id, now_dt)
                if path is None:
                    continue
                if len(path) - 1 > self.max_chain_length:
                    continue
                trusted.append(entity_id)
            return trusted

    # ------------------------------------------------------------------
    # Revocation
    # ------------------------------------------------------------------
    def revoke_entity(self, entity_id: str, *, cascade: bool = True,
                      reason: str = "manual") -> int:
        with self._lock:
            if entity_id in self._revoked:
                return 0
            if entity_id not in self._known_entities_locked():
                raise FederationError("FED_UNKNOWN_ENTITY", entity_id)

            if cascade:
                to_revoke: set[str] = set()
                pending = [entity_id]
                while pending:
                    current = pending.pop()
                    if current in to_revoke:
                        continue
                    to_revoke.add(current)
                    for statement in self._statements.values():
                        if statement["issuer"] == current:
                            pending.append(statement["subject"])
            else:
                to_revoke = {entity_id}

            new_ids = [item for item in to_revoke
                       if item not in self._revoked]
            for item in new_ids:
                self._revoked[item] = reason

            # Statements directly attached to a revoked entity can no longer
            # be used.  This also gives non-cascade revocation its required
            # "downstream loses the path without being revoked" behaviour.
            for key in list(self._statements):
                if key[0] in to_revoke or key[1] in to_revoke:
                    del self._statements[key]

            self._stats["revoked"] += len(new_ids)
            return len(new_ids)

    def is_revoked(self, entity_id: str) -> bool:
        with self._lock:
            return entity_id in self._revoked

    # ------------------------------------------------------------------
    # Maintenance, snapshots and statistics
    # ------------------------------------------------------------------
    def purge(self, now=None) -> int:
        with self._lock:
            now_dt = self._now(now)
            removed = 0
            for entity_id in list(self._anchors):
                if self._expired(self._anchors[entity_id], now_dt):
                    del self._anchors[entity_id]
                    removed += 1
            for entity_id in list(self._configs):
                if self._expired(self._configs[entity_id], now_dt):
                    del self._configs[entity_id]
                    removed += 1
            for key in list(self._statements):
                if self._expired(self._statements[key], now_dt):
                    del self._statements[key]
                    removed += 1
            return removed

    def snapshot(self) -> dict:
        with self._lock:
            anchors = {
                entity_id: copy.deepcopy(record)
                for entity_id, record in sorted(self._anchors.items())
            }
            configs = {
                entity_id: copy.deepcopy(record)
                for entity_id, record in sorted(self._configs.items())
            }
            statements = [
                copy.deepcopy(self._statements[key])
                for key in sorted(self._statements)
            ]
            return {
                "version": SNAPSHOT_VERSION,
                "config": {"max_chain_length": self.max_chain_length},
                "anchors": anchors,
                "configs": configs,
                "statements": statements,
                "revoked": sorted(self._revoked),
                "stats": dict(self._stats),
            }

    def restore(self, state: dict) -> None:
        with self._lock:
            try:
                normalized = self._normalize_snapshot(state)
            except Exception as exc:
                raise FederationError("FED_SNAPSHOT_INVALID", str(exc)) from exc

            # Commit only after the complete snapshot has been validated.
            self.max_chain_length = normalized["max_chain_length"]
            self._anchors = normalized["anchors"]
            self._configs = normalized["configs"]
            self._statements = normalized["statements"]
            self._revoked = normalized["revoked"]
            self._stats = normalized["stats"]

    def _normalize_snapshot(self, state: dict) -> dict:
        if not isinstance(state, dict) or state.get("version") != \
                SNAPSHOT_VERSION:
            raise ValueError("unsupported snapshot version")
        required = {"config", "anchors", "configs", "statements", "revoked",
                    "stats"}
        if not required.issubset(state):
            raise ValueError("missing snapshot fields")

        config = state["config"]
        if not isinstance(config, dict):
            raise ValueError("invalid config")
        max_len = config.get("max_chain_length")
        if (not isinstance(max_len, int) or isinstance(max_len, bool)
                or max_len < 0):
            raise ValueError("invalid max_chain_length")

        raw_anchors = state["anchors"]
        if not isinstance(raw_anchors, dict):
            raise ValueError("invalid anchors")
        anchors: dict[str, dict] = {}
        for entity_id, record in raw_anchors.items():
            if not _is_nonempty_string(entity_id) or not isinstance(record,
                                                                   dict):
                raise ValueError("invalid anchor entry")
            fp = record.get("jwks_fingerprint")
            expires_at = record.get("expires_at")
            if not _is_nonempty_string(fp):
                raise ValueError("invalid anchor fingerprint")
            _parse_instant(expires_at)
            if ("entity_id" in record and record["entity_id"] != entity_id):
                raise ValueError("anchor entity mismatch")
            anchors[entity_id] = {
                "entity_id": entity_id,
                "jwks_fingerprint": fp,
                "expires_at": expires_at,
            }

        raw_configs = state["configs"]
        if not isinstance(raw_configs, dict):
            raise ValueError("invalid configs")
        configs: dict[str, dict] = {}
        for entity_id, record in raw_configs.items():
            if not _is_nonempty_string(entity_id) or not isinstance(record,
                                                                   dict):
                raise ValueError("invalid entity configuration")
            issuer = record.get("issuer")
            fp = record.get("jwks_fingerprint")
            expires_at = record.get("expires_at")
            if (issuer != entity_id or not _is_nonempty_string(fp)):
                raise ValueError("invalid entity configuration")
            _parse_instant(expires_at)
            try:
                hints = self._normalize_hints(record.get("authority_hints"))
            except FederationError as exc:
                raise ValueError(str(exc)) from exc
            configs[entity_id] = {
                "entity_id": entity_id,
                "issuer": issuer,
                "jwks_fingerprint": fp,
                "authority_hints": hints,
                "metadata": copy.deepcopy(record.get("metadata")),
                "expires_at": expires_at,
            }

        raw_statements = state["statements"]
        if not isinstance(raw_statements, list):
            raise ValueError("invalid statements")
        statements: dict[tuple[str, str], dict] = {}
        for record in raw_statements:
            if not isinstance(record, dict):
                raise ValueError("invalid statement")
            issuer = record.get("issuer")
            subject = record.get("subject")
            fp = record.get("jwks_fingerprint")
            expires_at = record.get("expires_at")
            if (not _is_nonempty_string(issuer)
                    or not _is_nonempty_string(subject)
                    or issuer == subject
                    or not _is_nonempty_string(fp)):
                raise ValueError("invalid statement")
            _parse_instant(expires_at)
            key = (issuer, subject)
            if key in statements:
                raise ValueError("duplicate statement")
            statements[key] = {
                "issuer": issuer,
                "subject": subject,
                "jwks_fingerprint": fp,
                "metadata": copy.deepcopy(record.get("metadata")),
                "expires_at": expires_at,
            }

        raw_revoked = state["revoked"]
        revoked: dict[str, str] = {}
        if isinstance(raw_revoked, dict):
            revoked_items = raw_revoked.items()
        elif isinstance(raw_revoked, (list, tuple, set)):
            revoked_items = ((item, "manual") for item in raw_revoked)
        else:
            raise ValueError("invalid revoked list")
        for entity_id, reason in revoked_items:
            if not _is_nonempty_string(entity_id):
                raise ValueError("invalid revoked entity")
            revoked[entity_id] = str(reason)

        raw_stats = state["stats"]
        if not isinstance(raw_stats, dict) or set(raw_stats) != set(STAT_KEYS):
            raise ValueError("invalid stats")
        stats = {}
        for key in STAT_KEYS:
            value = raw_stats[key]
            if (not isinstance(value, int) or isinstance(value, bool)
                    or value < 0):
                raise ValueError("invalid stats")
            stats[key] = value

        return {
            "max_chain_length": max_len,
            "anchors": anchors,
            "configs": configs,
            "statements": statements,
            "revoked": revoked,
            "stats": stats,
        }

    def stats(self) -> dict:
        with self._lock:
            return dict(self._stats)

    def reset_stats(self) -> None:
        with self._lock:
            self._stats = {key: 0 for key in STAT_KEYS}
