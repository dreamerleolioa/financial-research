from __future__ import annotations

from datetime import date

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from ai_stock_sentinel.db.models import PositionEvent, PositionLifecyclePlan, UserPortfolio
from ai_stock_sentinel.db.session import Base
from ai_stock_sentinel.user_models.user import User


def _session() -> Session:
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine, tables=[User.__table__, UserPortfolio.__table__, PositionEvent.__table__, PositionLifecyclePlan.__table__])
    return Session(engine)


def test_position_event_reason_fields_can_be_saved_and_read() -> None:
    with _session() as session:
        session.add(User(id=1, google_sub="user-1", email="user@example.com"))
        session.add(UserPortfolio(
            id=42,
            user_id=1,
            position_group_id="group-reason",
            symbol="2330.TW",
            entry_price=900,
            quantity=100,
            entry_date=date(2026, 1, 1),
        ))
        session.add(PositionEvent(
            user_id=1,
            position_group_id="group-reason",
            symbol="2330.TW",
            event_type="initial_entry",
            event_date=date(2026, 1, 1),
            price=900,
            quantity=100,
            source_portfolio_id=42,
            note="Entered after breakout retest.",
            reason_category="technical",
            reason_code="breakout_confirmation",
            plan_adherence="yes",
            confidence_level="medium",
            source="user_recorded_at_event_time",
        ))
        session.commit()

        event = session.execute(select(PositionEvent)).scalar_one()

    assert event.reason_category == "technical"
    assert event.reason_code == "breakout_confirmation"
    assert event.plan_adherence == "yes"
    assert event.confidence_level == "medium"
    assert event.note == "Entered after breakout retest."


def test_lifecycle_plan_fields_can_be_saved_and_read_as_user_backfilled() -> None:
    with _session() as session:
        session.add(User(id=1, google_sub="user-1", email="user@example.com"))
        session.add(UserPortfolio(
            id=42,
            user_id=1,
            position_group_id="group-plan",
            symbol="2330.TW",
            entry_price=900,
            quantity=100,
            entry_date=date(2026, 1, 1),
        ))
        session.add(PositionLifecyclePlan(
            user_id=1,
            position_group_id="group-plan",
            symbol="2330.TW",
            source_portfolio_id=42,
            thesis="Institutional accumulation after base breakout.",
            setup_type="breakout",
            planned_holding_period="swing",
            planned_invalidation="Close below MA20 with institutional distribution.",
            planned_stop_price=880,
            planned_target_or_scale_out_rule="Trim half near prior resistance.",
            planned_risk_amount=5000,
            planned_risk_pct=1.25,
            position_sizing_rationale="Initial probe only.",
            source="user_backfilled",
            created_after_entry=True,
        ))
        session.commit()

        plan = session.execute(select(PositionLifecyclePlan)).scalar_one()

    assert plan.thesis == "Institutional accumulation after base breakout."
    assert plan.setup_type == "breakout"
    assert plan.planned_holding_period == "swing"
    assert plan.planned_invalidation == "Close below MA20 with institutional distribution."
    assert float(plan.planned_stop_price) == 880.0
    assert plan.planned_target_or_scale_out_rule == "Trim half near prior resistance."
    assert float(plan.planned_risk_amount) == 5000.0
    assert float(plan.planned_risk_pct) == 1.25
    assert plan.position_sizing_rationale == "Initial probe only."
    assert plan.source == "user_backfilled"
    assert plan.created_after_entry is True


def test_missing_lifecycle_plan_does_not_create_intent_defaults() -> None:
    with _session() as session:
        session.add(User(id=1, google_sub="user-1", email="user@example.com"))
        session.add(UserPortfolio(
            id=42,
            user_id=1,
            position_group_id="group-no-plan",
            symbol="2330.TW",
            entry_price=900,
            quantity=100,
            entry_date=date(2026, 1, 1),
        ))
        session.add(PositionEvent(
            user_id=1,
            position_group_id="group-no-plan",
            symbol="2330.TW",
            event_type="initial_entry",
            event_date=date(2026, 1, 1),
            price=900,
            quantity=100,
            source_portfolio_id=42,
            source="user_recorded_at_event_time",
        ))
        session.commit()

        event = session.execute(select(PositionEvent)).scalar_one()
        plans = session.execute(select(PositionLifecyclePlan)).scalars().all()

    assert event.reason_category is None
    assert event.reason_code is None
    assert event.plan_adherence is None
    assert event.confidence_level is None
    assert plans == []
