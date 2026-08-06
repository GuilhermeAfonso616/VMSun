from app.db.models import Camera, Event, IncidentTimeline, User


def test_incident_http_workflow_summary_and_timeline(api_context):
    supervisor = User(username="incident-supervisor", password_hash="hash", role="supervisor", is_active=True)
    operator = User(username="incident-operator", password_hash="hash", role="operator", is_active=True)
    camera = Camera(name="Entrada", ip="10.0.0.20", username="device", password="secret")
    api_context.db.add_all([supervisor, operator, camera])
    api_context.db.flush()
    event = Event(
        camera_id=camera.id,
        event_type="person_entered_roi",
        severity="high",
        status="new",
        lifecycle_action="open",
        alarm_eligible=True,
        is_alarm_active=True,
    )
    api_context.db.add(event)
    api_context.db.commit()
    api_context.selected_user["value"] = supervisor

    assigned = api_context.client.post(
        f"/api/events/{event.id}/assign", json={"assignee_user_id": operator.id}
    )
    acknowledged = api_context.client.post(f"/api/events/{event.id}/acknowledge")
    commented = api_context.client.post(
        f"/api/events/{event.id}/comments", json={"comment": "Em verificacao"}
    )
    closed = api_context.client.post(
        f"/api/events/{event.id}/close",
        json={"resolution_code": "false_alarm", "comment": "Reflexo confirmado"},
    )
    timeline = api_context.client.get(f"/api/events/{event.id}/timeline")
    summary = api_context.client.get("/api/events/incidents/summary")

    assert assigned.status_code == 200
    assert acknowledged.json()["status"] == "acknowledged"
    assert commented.status_code == 200
    assert closed.json()["resolution_code"] == "false_alarm"
    assert [item["action"] for item in timeline.json()] == [
        "created", "assigned", "acknowledged", "commented", "closed"
    ]
    assert summary.json()["open"] == 0
    assert api_context.db.query(IncidentTimeline).filter(IncidentTimeline.event_id == event.id).count() == 5


def test_operator_cannot_reopen_incident(api_context):
    operator = User(username="reopen-operator", password_hash="hash", role="operator", is_active=True)
    event = Event(
        camera_id=1,
        event_type="intrusion",
        severity="high",
        status="closed",
        lifecycle_action="open",
        alarm_eligible=True,
        is_alarm_active=False,
    )
    api_context.db.add_all([operator, event])
    api_context.db.commit()
    api_context.selected_user["value"] = operator

    response = api_context.client.post(
        f"/api/events/{event.id}/reopen", json={"comment": "tentativa"}
    )
    assert response.status_code == 403


def test_manual_incident_checklist_details_and_correlation_http(api_context):
    supervisor = User(username="manual-supervisor", password_hash="hash", role="supervisor", is_active=True)
    camera = Camera(name="Perimetro", ip="10.0.0.31", username="device", password="secret")
    api_context.db.add_all([supervisor, camera])
    api_context.db.flush()
    related = Event(
        camera_id=camera.id,
        event_type="intrusion",
        severity="high",
        status="new",
        alarm_eligible=True,
        is_alarm_active=True,
    )
    api_context.db.add(related)
    api_context.db.commit()
    api_context.selected_user["value"] = supervisor

    created = api_context.client.post(
        "/api/events/incidents",
        json={
            "camera_id": camera.id,
            "title": "Inspecao manual",
            "priority": "high",
            "team": "Ronda",
            "assignee_user_id": supervisor.id,
        },
    )
    assert created.status_code == 200
    incident_id = created.json()["event_id"]

    details = api_context.client.patch(
        f"/api/events/{incident_id}/details",
        json={"priority": "critical", "team": "Seguranca"},
    )
    checklist = api_context.client.patch(
        f"/api/events/{incident_id}/checklist/verify_scene",
        json={"completed": True},
    )
    correlated = api_context.client.post(
        f"/api/events/{incident_id}/correlate", json={"event_ids": [related.id]}
    )
    incident = api_context.client.get(f"/api/events/{incident_id}/incident")

    assert details.json()["priority"] == "critical"
    assert checklist.json()["checklist"][0]["completed"] is True
    assert correlated.json()["linked_event_ids"] == [related.id]
    assert {item["id"] for item in incident.json()["related_events"]} == {incident_id, related.id}
