"""Shared contract for the JSON registers: 201/200/404/409/422, audit rows, stamps, no caching.

Concrete classes mix ``JsonRegisterContract`` into ``RegisterRoutesBase`` and set
the class attributes below. The mixin is not a ``TestCase`` so discovery never
runs it on its own.
"""

from __future__ import annotations

from register_routes import RegisterRoutesBase

from app.models import RoleEnum


class JsonRegisterContract:
    BASE = ""  # URL prefix with trailing slash, e.g. "/rol_responsabilidad/"
    MODEL = None
    PK = ""  # primary key attribute, e.g. "id_rol"
    CREATE: dict = {}  # valid create payload
    UPDATE: dict = {}  # valid partial update (must change a stored value)
    UPDATED_KEY = ""  # key of UPDATE, echoed back in the response
    SEED: dict = {}  # service payload used to seed rows (all keys valid)
    SERVICE = None  # module with create/update/... for seeding through the service
    BAD = ()  # payloads rejected with 422 on create (besides unknown fields)
    UNIQUE = None  # (key, value) that collides with SEED, or None
    PAGE_KEY = ""  # a key whose value tells seeded rows apart (listing order)
    PAGE_VALUES = ()  # three create payloads with distinct PAGE_KEY, for pagination

    def _post(self, payload):
        return self.client.post(self.BASE, json=payload)

    def _rows(self):
        return self.client.get(self.BASE).get_json()

    def test_create_returns_201_with_the_resource_stamps_and_audit_row(self) -> None:
        self.login()
        response = self._post(self.CREATE)
        self.assertEqual(201, response.status_code)
        body = response.get_json()
        for key, value in self.CREATE.items():
            self.assertEqual(value, body[key])
        self.assertEqual(body, self._rows()[0])  # same shape as the GET listing
        with self.app.app_context():
            created = self.MODEL.query.one()
            self.assertEqual(getattr(created, self.PK), body[self.PK])
            self.assertEqual(self.ids[RoleEnum.OPERATIVO], created.created_by_id)
            self.assertIsNotNone(created.created_at)
        self.assertEqual(["create"], self.actions())

    def test_update_returns_200_audits_changes_and_skips_noops(self) -> None:
        record_id = self.seed_with(self.SERVICE, self.SEED)
        self.login()
        url = f"{self.BASE}{record_id}"
        response = self.client.put(url, json=self.UPDATE)
        self.assertEqual(200, response.status_code)
        self.assertEqual(self.UPDATE[self.UPDATED_KEY], response.get_json()[self.UPDATED_KEY])
        self.assertEqual(200, self.client.put(url, json=self.UPDATE).status_code)  # unchanged
        self.assertEqual(["create", "update"], self.actions())
        with self.app.app_context():
            stored = self.MODEL.query.one()
            self.assertEqual(self.ids[RoleEnum.OPERATIVO], stored.updated_by_id)

    def test_delete_keeps_its_status_and_body_and_snapshots_the_row(self) -> None:
        record_id = self.seed_with(self.SERVICE, self.SEED)
        self.login()
        response = self.client.delete(f"{self.BASE}{record_id}")
        self.assertEqual(200, response.status_code)
        self.assertEqual({"message"}, set(response.get_json()))
        self.assertEqual(0, self.count(self.MODEL))
        self.assertEqual(["create", "delete"], self.actions())

    def test_unknown_records_answer_404(self) -> None:
        self.login()
        json_accept = {"Accept": "application/json"}
        for response in (self.client.put(f"{self.BASE}999", json=self.UPDATE),
                         self.client.delete(f"{self.BASE}999", headers=json_accept)):
            self.assertEqual(404, response.status_code)
            self.assertIn("error", response.get_json())  # the domain body for JSON clients
        plain = self.client.delete(f"{self.BASE}999")  # no Accept: the route-404 body as before
        self.assertEqual((404, {"message": "Recurso no encontrado"}), (plain.status_code, plain.get_json()))

    def test_invalid_payloads_answer_422_without_writing(self) -> None:
        self.login()
        for payload in (*self.BAD, self.CREATE | {"campo_inventado": 1}, ["no", "es", "un", "objeto"]):
            with self.subTest(payload=payload):
                response = self._post(payload)
                self.assertEqual(422, response.status_code)
                self.assertIn("error", response.get_json())
        record_id = self.seed_with(self.SERVICE, self.SEED)
        response = self.client.put(f"{self.BASE}{record_id}", json={"campo_inventado": 1})
        self.assertEqual(422, response.status_code)
        self.assertEqual(1, self.count(self.MODEL))
        self.assertEqual(["create"], self.actions())

    def test_missing_body_keeps_answering_400(self) -> None:
        self.login()
        self.assertEqual(400, self._post({}).status_code)
        self.assertEqual(400, self.client.put(f"{self.BASE}1", json={}).status_code)

    def test_a_duplicate_answers_409_and_leaves_the_session_usable(self) -> None:
        if self.UNIQUE is None:
            self.skipTest("register has no unique column")
        key, value = self.UNIQUE
        self.seed_with(self.SERVICE, self.SEED)
        self.login()
        response = self._post(self.CREATE | {key: value})
        self.assertEqual(409, response.status_code)
        self.assertIn("error", response.get_json())
        other = self.seed_with(self.SERVICE, self.SEED | {key: f"{value} bis"})
        self.assertEqual(409, self.client.put(f"{self.BASE}{other}", json={key: value}).status_code)
        self.assertEqual(2, self.count(self.MODEL))

    def test_listing_is_paginated_in_id_order_and_never_cached(self) -> None:
        self.login()
        self.assertEqual([], self._rows())
        for payload in self.PAGE_VALUES:
            self.assertEqual(201, self._post(payload).status_code)
        keys = [row[self.PAGE_KEY] for row in self._rows()]  # visible right after the write
        self.assertEqual([p[self.PAGE_KEY] for p in self.PAGE_VALUES], keys)
        second = self.client.get(f"{self.BASE}?per_page=2&page=2").get_json()
        self.assertEqual(keys[2:], [row[self.PAGE_KEY] for row in second])
        self.client.put(f"{self.BASE}{self._rows()[0][self.PK]}", json=self.UPDATE)
        self.assertEqual(self.UPDATE[self.UPDATED_KEY], self._rows()[0][self.UPDATED_KEY])
        self.client.delete(f"{self.BASE}{self._rows()[0][self.PK]}")
        self.assertEqual(len(keys) - 1, len(self._rows()))

    def test_out_of_range_paging_gives_200_with_sane_bounds(self) -> None:
        self.login()
        for payload in self.PAGE_VALUES:
            self.assertEqual(201, self._post(payload).status_code)
        total = len(self.PAGE_VALUES)
        for query in ("page=0", "page=-3", "page=abc", "page=1.5", "page=" + "9" * 40,
                      "per_page=0", "per_page=-1", "per_page=abc", "per_page=" + "9" * 40,
                      "page=0&per_page=0", "page=" + "9" * 40 + "&per_page=" + "9" * 40):
            with self.subTest(query=query):
                response = self.client.get(f"{self.BASE}?{query}")
                self.assertEqual(200, response.status_code)
                self.assertLessEqual(len(response.get_json()), total)
        # A page below 1 is the first page; a page past the end is simply empty.
        self.assertEqual(self._rows(), self.client.get(f"{self.BASE}?page=-3").get_json())
        self.assertEqual([], self.client.get(f"{self.BASE}?page=" + "9" * 40).get_json())

    def test_page_size_is_clamped_to_the_documented_maximum(self) -> None:
        from unittest.mock import patch

        from app.services import crud

        self.assertEqual(100, crud.MAX_PER_PAGE)
        self.login()
        for payload in self.PAGE_VALUES:
            self.assertEqual(201, self._post(payload).status_code)
        with patch.object(crud, "MAX_PER_PAGE", 2):
            rows = self.client.get(f"{self.BASE}?per_page=50").get_json()
        self.assertEqual(2, len(rows))
