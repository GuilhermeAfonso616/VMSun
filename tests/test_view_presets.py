import unittest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool
from sqlalchemy.orm import sessionmaker

from app.db.base import Base
from app.db.models import User, ViewPreset, TemporalSequence
from app.api.routes import (
    ViewPresetIn,
    TemporalSequenceIn,
    TemporalSequenceStep,
    get_view_presets,
    create_or_update_view_preset,
    delete_view_preset,
    get_temporal_sequences,
    create_or_update_temporal_sequence,
    delete_temporal_sequence,
)

class TestViewPresetsAndSequences(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine(
            "sqlite://",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        Base.metadata.create_all(self.engine)
        self.SessionLocal = sessionmaker(bind=self.engine)
        self.db = self.SessionLocal()

        self.user = User(id=1, username="operator", password_hash="dummy_hash", role="operator", is_active=True)
        self.other_user = User(id=2, username="other_operator", password_hash="dummy_hash", role="operator", is_active=True)
        self.admin = User(id=3, username="admin", password_hash="dummy_hash", role="admin", is_active=True)
        self.db.add_all([self.user, self.other_user, self.admin])
        self.db.commit()

    def tearDown(self):
        self.db.close()
        Base.metadata.drop_all(self.engine)
        self.engine.dispose()

    def test_create_and_get_view_preset(self):
        # 1. Create a view preset
        payload = ViewPresetIn(
            id="view_123",
            name="Main Guarita",
            grid_size=4,
            camera_ids=[1, 2, None, 4],
            hide_offline=True,
            boxes_enabled=False
        )
        res = create_or_update_view_preset(payload, self.user, self.db)
        self.assertEqual(res["status"], "success")

        # 2. Query it back
        presets = get_view_presets(self.user, self.db)
        self.assertEqual(len(presets), 1)
        preset = presets[0]
        self.assertEqual(preset.id, "view_123")
        self.assertEqual(preset.name, "Main Guarita")
        self.assertEqual(preset.grid_size, 4)
        self.assertEqual(preset.camera_ids, [1, 2, None, 4])
        self.assertEqual(preset.hide_offline, True)
        self.assertEqual(preset.boxes_enabled, False)
        self.assertTrue(preset.can_manage)

    def test_delete_view_preset(self):
        # 1. Create a preset
        payload = ViewPresetIn(
            id="view_456",
            name="Test",
            grid_size=16,
            camera_ids=[]
        )
        create_or_update_view_preset(payload, self.user, self.db)

        # 2. Delete it
        res = delete_view_preset("view_456", self.user, self.db)
        self.assertEqual(res["status"], "success")

        # 3. Ensure list is empty
        presets = get_view_presets(self.user, self.db)
        self.assertEqual(len(presets), 0)

    def test_create_and_get_temporal_sequence(self):
        # 1. Create a temporal sequence
        payload = TemporalSequenceIn(
            id="seq_111",
            name="Night Patrol",
            steps=[
                TemporalSequenceStep(viewId="view_123", duration=10),
                TemporalSequenceStep(viewId="view_456", duration=5)
            ]
        )
        res = create_or_update_temporal_sequence(payload, self.user, self.db)
        self.assertEqual(res["status"], "success")

        # 2. Query it back
        seqs = get_temporal_sequences(self.user, self.db)
        self.assertEqual(len(seqs), 1)
        seq = seqs[0]
        self.assertEqual(seq.id, "seq_111")
        self.assertEqual(seq.name, "Night Patrol")
        self.assertEqual(len(seq.steps), 2)
        self.assertEqual(seq.steps[0].viewId, "view_123")
        self.assertEqual(seq.steps[0].duration, 10)

        # 3. Delete sequence
        del_res = delete_temporal_sequence("seq_111", self.user, self.db)
        self.assertEqual(del_res["status"], "success")
        self.assertEqual(len(get_temporal_sequences(self.user, self.db)), 0)

    def test_private_view_is_not_visible_or_manageable_by_another_user(self):
        create_or_update_view_preset(
            ViewPresetIn(
                id="view_private",
                name="Private",
                grid_size=4,
                camera_ids=[1],
            ),
            self.user,
            self.db,
        )

        self.assertEqual(get_view_presets(self.other_user, self.db), [])
        with self.assertRaises(HTTPException) as create_error:
            create_or_update_view_preset(
                ViewPresetIn(
                    id="view_private",
                    name="Takeover",
                    grid_size=16,
                    camera_ids=[],
                ),
                self.other_user,
                self.db,
            )
        self.assertEqual(create_error.exception.status_code, 403)

        with self.assertRaises(HTTPException) as delete_error:
            delete_view_preset("view_private", self.other_user, self.db)
        self.assertEqual(delete_error.exception.status_code, 403)

    def test_admin_can_share_view_without_granting_edit_permission(self):
        create_or_update_view_preset(
            ViewPresetIn(
                id="view_shared",
                name="Shared by admin",
                grid_size=9,
                camera_ids=[1, 2],
                is_shared=True,
            ),
            self.admin,
            self.db,
        )

        presets = get_view_presets(self.other_user, self.db)
        self.assertEqual(len(presets), 1)
        self.assertTrue(presets[0].is_shared)
        self.assertEqual(presets[0].owner_username, "admin")
        self.assertFalse(presets[0].can_manage)

        with self.assertRaises(HTTPException) as delete_error:
            delete_view_preset("view_shared", self.other_user, self.db)
        self.assertEqual(delete_error.exception.status_code, 403)

    def test_only_admin_can_share_view(self):
        with self.assertRaises(HTTPException) as error:
            create_or_update_view_preset(
                ViewPresetIn(
                    id="view_not_allowed",
                    name="Operator sharing",
                    grid_size=4,
                    camera_ids=[],
                    is_shared=True,
                ),
                self.user,
                self.db,
            )
        self.assertEqual(error.exception.status_code, 403)

    def test_admin_can_share_sequence_without_granting_edit_permission(self):
        create_or_update_temporal_sequence(
            TemporalSequenceIn(
                id="sequence_shared",
                name="Shared patrol",
                steps=[TemporalSequenceStep(viewId="view_shared", duration=10)],
                is_shared=True,
            ),
            self.admin,
            self.db,
        )

        sequences = get_temporal_sequences(self.other_user, self.db)
        self.assertEqual(len(sequences), 1)
        self.assertTrue(sequences[0].is_shared)
        self.assertEqual(sequences[0].owner_username, "admin")
        self.assertFalse(sequences[0].can_manage)
