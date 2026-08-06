import unittest
from datetime import datetime
from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool
from sqlalchemy.orm import sessionmaker

from app.db.base import Base
from app.db.models import User, AuditLog
from app.api.routes import list_audit_logs

class TestAuditLogVisibility(unittest.TestCase):
    def setUp(self):
        # Cria banco de dados em memória para teste
        self.engine = create_engine(
            "sqlite://",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        Base.metadata.create_all(self.engine)
        self.SessionLocal = sessionmaker(bind=self.engine)
        self.db = self.SessionLocal()

        # Cria usuários de teste de cada nível
        self.u_dev = User(id=1, username="dev_user", password_hash="hash", role="dev", is_active=True)
        self.u_admin = User(id=2, username="admin_user", password_hash="hash", role="admin", is_active=True)
        self.u_supervisor = User(id=3, username="supervisor_user", password_hash="hash", role="supervisor", is_active=True)
        self.u_operator = User(id=4, username="operator_user", password_hash="hash", role="operator", is_active=True)
        self.u_viewer = User(id=5, username="viewer_user", password_hash="hash", role="viewer", is_active=True)

        self.db.add_all([self.u_dev, self.u_admin, self.u_supervisor, self.u_operator, self.u_viewer])
        self.db.commit()

        # Cria logs de auditoria correspondentes a cada usuário
        self.log_dev = AuditLog(id=1, user_id=self.u_dev.id, username=self.u_dev.username, action="login", details="Dev Log")
        self.log_admin = AuditLog(id=2, user_id=self.u_admin.id, username=self.u_admin.username, action="login", details="Admin Log")
        self.log_supervisor = AuditLog(id=3, user_id=self.u_supervisor.id, username=self.u_supervisor.username, action="login", details="Supervisor Log")
        self.log_operator = AuditLog(id=4, user_id=self.u_operator.id, username=self.u_operator.username, action="login", details="Operator Log")
        self.log_viewer = AuditLog(id=5, user_id=self.u_viewer.id, username=self.u_viewer.username, action="login", details="Viewer Log")
        self.log_anonymous = AuditLog(id=6, user_id=None, username="anonymous", action="login_failed", details="Anonymous Log")

        self.db.add_all([self.log_dev, self.log_admin, self.log_supervisor, self.log_operator, self.log_viewer, self.log_anonymous])
        self.db.commit()

    def tearDown(self):
        self.db.close()
        Base.metadata.drop_all(self.engine)
        self.engine.dispose()

    def test_dev_visibility(self):
        # Dev deve ver todos os logs (6 registros)
        logs = list_audit_logs(db=self.db, current_user=self.u_dev, username=None, action=None, limit=50, offset=0)
        log_ids = {l.id for l in logs}
        self.assertEqual(len(log_ids), 6)

    def test_admin_visibility(self):
        # Admin deve ver todos os logs (6 registros)
        logs = list_audit_logs(db=self.db, current_user=self.u_admin, username=None, action=None, limit=50, offset=0)
        log_ids = {l.id for l in logs}
        self.assertEqual(len(log_ids), 6)

    def test_supervisor_visibility(self):
        # Supervisor deve ver seu próprio log e logs de operador e viewer
        # Logs esperados: Supervisor Log (id=3), Operator Log (id=4), Viewer Log (id=5)
        logs = list_audit_logs(db=self.db, current_user=self.u_supervisor, username=None, action=None, limit=50, offset=0)
        log_ids = {l.id for l in logs}
        self.assertIn(3, log_ids)
        self.assertIn(4, log_ids)
        self.assertIn(5, log_ids)
        self.assertNotIn(1, log_ids) # Dev Log
        self.assertNotIn(2, log_ids) # Admin Log
        self.assertNotIn(6, log_ids) # Anonymous/None Log
        self.assertEqual(len(log_ids), 3)

    def test_operator_visibility(self):
        # Operador deve ver seu próprio log e logs de viewer
        # Logs esperados: Operator Log (id=4), Viewer Log (id=5)
        logs = list_audit_logs(db=self.db, current_user=self.u_operator, username=None, action=None, limit=50, offset=0)
        log_ids = {l.id for l in logs}
        self.assertIn(4, log_ids)
        self.assertIn(5, log_ids)
        self.assertNotIn(3, log_ids) # Supervisor Log
        self.assertNotIn(2, log_ids) # Admin Log
        self.assertEqual(len(log_ids), 2)

    def test_viewer_visibility(self):
        # Viewer deve ver apenas o seu próprio log
        # Logs esperados: Viewer Log (id=5)
        logs = list_audit_logs(db=self.db, current_user=self.u_viewer, username=None, action=None, limit=50, offset=0)
        log_ids = {l.id for l in logs}
        self.assertEqual(list(log_ids), [5])
