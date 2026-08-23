"""Migration must not silently lose or conflate businesses.

Before these guards, contacts.json held 1,355 businesses and the database held
1,172. Two defects were responsible:

1. A shared email was treated as identity. A single booking-platform support
   address served six separate barbershops, and emails scraped out of embedded
   font licences appeared on many sites; both merged unrelated businesses.
2. The merge path deleted the losing row, so repeated migrations oscillated —
   inserting businesses on one run and destroying them on the next.

A Google Place ID identifies a business. An email address does not.
"""
import tempfile
import unittest
from pathlib import Path

from winston.repository import WinstonRepository


class ContactIdentityTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.repo = WinstonRepository(Path(self.temp.name) / "identity.db")
        self.repo.initialize()

    def tearDown(self):
        self.temp.cleanup()

    def _count(self):
        return self.repo.connect().execute("SELECT COUNT(*) c FROM contacts").fetchone()["c"]

    def test_businesses_sharing_an_email_stay_distinct(self):
        """Six barbershops share one booking-platform support address. Still six businesses."""
        shared = "support@bookingplatform.example"
        ids = set()
        for n in range(6):
            contact_id, created = self.repo.upsert_contact(
                {"name": f"Barbershop {n}", "email": shared,
                 "place_id": f"place-{n}", "address": f"{n} Main St"}, "test")
            ids.add(contact_id)
            self.assertTrue(created, f"business {n} should have been created, not merged")
        self.assertEqual(len(ids), 6)
        self.assertEqual(self._count(), 6)

    def test_the_same_place_id_still_deduplicates(self):
        for _ in range(3):
            self.repo.upsert_contact(
                {"name": "Same Co", "email": "a@example.com",
                 "place_id": "place-x", "address": "1 Main St"}, "test")
        self.assertEqual(self._count(), 1)

    def test_email_only_contacts_still_deduplicate(self):
        """Without a Place ID, email remains the only identity available."""
        for _ in range(3):
            self.repo.upsert_contact({"name": "Email Only", "email": "solo@example.com"}, "test")
        self.assertEqual(self._count(), 1)

    def test_repeated_migration_is_stable(self):
        """The oscillation bug: rows inserted on one pass, deleted on the next."""
        records = [
            {"name": f"Biz {n}", "email": "shared@example.com",
             "place_id": f"p{n}", "address": f"{n} Road"}
            for n in range(5)
        ]
        records.append({"name": "Solo", "email": "solo@example.com", "place_id": "p-solo"})

        counts = []
        for _ in range(4):
            for record in records:
                self.repo.upsert_contact(record, "contacts.json")
            counts.append(self._count())

        self.assertEqual(len(set(counts)), 1, f"contact count must not oscillate, saw {counts}")
        self.assertEqual(counts[0], 6)

    def test_no_business_is_overwritten_by_another(self):
        """The corruption case: a row keeping its id while its content is replaced."""
        first, _ = self.repo.upsert_contact(
            {"name": "Gentlemen's Barbershop", "email": "shared@bookingplatform.example",
             "place_id": "place-a", "address": "205 Johnson Ave"}, "test")
        self.repo.upsert_contact(
            {"name": "Hello Beautiful Braids", "email": "shared@bookingplatform.example",
             "place_id": "place-b", "address": "820 St Anns Ave"}, "test")

        row = self.repo.connect().execute(
            "SELECT name, place_id FROM contacts WHERE id=?", (first,)).fetchone()
        self.assertEqual(row["name"], "Gentlemen's Barbershop",
                         "the original business was overwritten by a different one")
        self.assertEqual(row["place_id"], "place-a")

    def test_no_duplicate_place_ids_are_ever_created(self):
        for n in range(4):
            self.repo.upsert_contact(
                {"name": f"B{n}", "email": f"e{n}@example.com", "place_id": "same-place"}, "test")
        duplicates = self.repo.connect().execute(
            """SELECT COUNT(*) c FROM (SELECT place_id FROM contacts
               WHERE place_id != '' GROUP BY place_id HAVING COUNT(*) > 1)""").fetchone()["c"]
        self.assertEqual(duplicates, 0)

    def test_merging_a_legacy_email_only_row_preserves_dependents(self):
        """An email-only legacy row genuinely is the same business; merge must keep history."""
        legacy_id, _ = self.repo.upsert_contact({"name": "Legacy", "email": "biz@example.com"}, "followups.json")
        draft_id = self.repo.create_draft(legacy_id, "s", "b")

        merged_id, _ = self.repo.upsert_contact(
            {"name": "Legacy", "email": "biz@example.com", "place_id": "place-legacy"}, "contacts.json")

        draft = self.repo.get_draft(draft_id)
        self.assertIsNotNone(draft, "the draft must survive the merge")
        self.assertEqual(draft["contact_id"], merged_id, "dependents must be repointed, not orphaned")


if __name__ == "__main__":
    unittest.main()
