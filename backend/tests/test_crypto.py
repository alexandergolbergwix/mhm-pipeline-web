"""Smoke tests for the crypto modules — pure unit tests, no DB."""

from __future__ import annotations

import pytest

from app.crypto import index, kek, pii, secrets


class TestPiiRoundTrip:
    def test_round_trip_ascii(self) -> None:
        ct = pii.encrypt_pii("alice@example.org")
        assert pii.decrypt_pii(ct) == "alice@example.org"

    def test_round_trip_unicode(self) -> None:
        plaintext = "אלוהיים שלום — UTF-8 PII"
        assert pii.decrypt_pii(pii.encrypt_pii(plaintext)) == plaintext

    def test_distinct_ciphertexts_for_same_plaintext(self) -> None:
        a = pii.encrypt_pii("same")
        b = pii.encrypt_pii("same")
        assert a != b  # randomised nonce → distinct ciphertexts


class TestBlindIndex:
    def test_deterministic_case_and_whitespace_insensitive(self) -> None:
        assert index.blind_index("Alice@Example.org") == index.blind_index(" alice@example.org ")

    def test_distinct_emails_distinct_indexes(self) -> None:
        assert index.blind_index("a@x.io") != index.blind_index("b@x.io")


class TestKekAndSecrets:
    def test_kek_is_deterministic_given_password_and_salt(self) -> None:
        salt = b"\x00" * 16
        k1 = kek.derive_kek("hunter2", salt=salt)
        k2 = kek.derive_kek("hunter2", salt=salt)
        assert k1 == k2
        assert len(k1) == 32

    def test_kek_changes_with_password_or_salt(self) -> None:
        s = b"\x01" * 16
        assert kek.derive_kek("aaa", salt=s) != kek.derive_kek("bbb", salt=s)
        assert kek.derive_kek("aaa", salt=s) != kek.derive_kek("aaa", salt=b"\x02" * 16)

    def test_session_wrap_round_trip(self) -> None:
        k = kek.derive_kek("p", salt=b"\x00" * 16)
        secret = kek.new_session_secret()
        ct, nonce = kek.wrap_kek(k, secret)
        assert kek.unwrap_kek(ct, nonce, secret) == k

    def test_secret_envelope_round_trip(self) -> None:
        k = kek.derive_kek("p", salt=b"\x00" * 16)
        wrapped = secrets.wrap_secret("AIzaTestGeminiKey123", kek=k)
        assert secrets.unwrap_secret(wrapped, kek=k) == "AIzaTestGeminiKey123"

    def test_wrong_kek_fails_unwrap(self) -> None:
        k1 = kek.derive_kek("p1", salt=b"\x00" * 16)
        k2 = kek.derive_kek("p2", salt=b"\x00" * 16)
        wrapped = secrets.wrap_secret("secret-value", kek=k1)
        with pytest.raises(Exception):  # InvalidTag → covers any AESGCM error
            secrets.unwrap_secret(wrapped, kek=k2)
