from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest
from sqlalchemy import Engine, select
from sqlalchemy.orm import Session

from src.db import create_database_engine, initialize_database
from src.models import (
    Application,
    Company,
    CompanyData,
    JobOffer,
    JobOfferData,
    ProfileData,
    ResumeVersion,
    SkillRating,
)
from src.services.applications import ApplicationNotFound, list_applications
from src.services.companies import CompanyNotFound, create_company
from src.services.cover_letters import get_cover_letter, save_cover_letter
from src.services.job_offers import OfferNotFound, create_offer, list_offers
from src.services.send_queue import (
    OfferAlreadySent,
    SpontaneousAlreadySent,
    average_prep_seconds,
    build_send_queue,
    build_spontaneous_queue,
    clamp_prep_seconds,
    complete_offer_description,
    contact_search_links,
    due_follow_ups,
    is_probably_truncated,
    mark_followed_up,
    mark_offer_sent,
    mark_spontaneous_sent,
    set_offer_status,
)
from src.services.tailored_resumes import get_tailored_resume


@pytest.fixture
def engine(tmp_path: Path) -> Engine:
    database = create_database_engine(tmp_path / "queue.sqlite3")
    initialize_database(database)
    return database


def _profile() -> ProfileData:
    return ProfileData(
        target_role="Développeur backend",
        skills=[
            SkillRating(name="PHP", category="Forte", level_min=8, level_max=8),
            SkillRating(name="Symfony", category="Forte", level_min=8, level_max=8),
        ],
    )


def _offer(title: str, description: str, **extra: str) -> JobOfferData:
    return JobOfferData(
        title=title,
        company=extra.pop("company", "Atelier Tech"),
        location=extra.pop("location", "Nice"),
        url=extra.pop("url", f"https://example.com/jobs/{title.replace(' ', '-')}"),
        description=description,
        **extra,
    )


def test_queue_ranks_by_score_and_hides_low_ignored_and_sent_offers(engine: Engine) -> None:
    good = create_offer(engine, _offer("Développeur PHP Symfony", "PHP et Symfony requis."))
    weak = create_offer(engine, _offer("Comptable", "Tenue de comptabilité."))
    ignored = create_offer(engine, _offer("Développeur PHP", "PHP.", status="Écartée"))
    sent = create_offer(engine, _offer("Développeur Symfony", "Symfony."))
    mark_offer_sent(engine, sent.id)

    queue = build_send_queue(engine, _profile(), min_score=40)

    assert [item.offer.id for item in queue] == [good.id]
    assert queue[0].score >= 40
    assert weak.id not in [item.offer.id for item in queue]
    assert ignored.id not in [item.offer.id for item in queue]


def test_starred_offer_stays_visible_below_threshold_and_comes_first(engine: Engine) -> None:
    good = create_offer(engine, _offer("Développeur PHP Symfony", "PHP et Symfony requis."))
    starred = create_offer(engine, _offer("Comptable", "Comptabilité.", status="Intéressante"))

    queue = build_send_queue(engine, _profile(), min_score=40)

    assert [item.offer.id for item in queue] == [starred.id, good.id]


def test_queue_reports_which_documents_exist(engine: Engine) -> None:
    offer = create_offer(engine, _offer("Développeur PHP Symfony", "PHP et Symfony."))
    save_cover_letter(engine, "Madame, Monsieur,\n\nBonjour.", job_offer_id=offer.id)

    [item] = build_send_queue(engine, _profile())

    assert item.has_letter is True
    assert item.has_resume is False
    assert item.is_ready is False


def test_mark_sent_creates_company_application_and_follow_up(engine: Engine) -> None:
    offer = create_offer(engine, _offer("Développeur PHP", "PHP.", location="Nice"))

    application = mark_offer_sent(engine, offer.id, sent_on=date(2026, 10, 6))

    assert application.status == "Envoyée"
    assert application.applied_on == "2026-10-06"
    assert application.next_action_on == "2026-10-13"
    [stored_offer] = list_offers(engine)
    assert stored_offer.status == "Candidature liée"
    with Session(engine) as session:
        company = session.get(Company, application.company_id)
        assert company is not None
        assert company.name == "Atelier Tech"
        assert company.source_url.startswith("https://example.com/jobs/")
        assert company.public_contact_email is None
        assert stored_offer.company_id == company.id
    assert len(list_applications(engine)) == 1


def test_mark_sent_reuses_existing_company_and_rejects_second_send(engine: Engine) -> None:
    existing = create_company(
        engine,
        CompanyData(
            name="Atelier Tech",
            location="Cannes",
            source_url="https://atelier.example/equipe",
            development_evidence="Équipe de développement citée sur le site.",
        ),
    )
    first = create_offer(engine, _offer("Développeur PHP", "PHP."))
    second = create_offer(engine, _offer("Développeur Symfony", "Symfony."))

    one = mark_offer_sent(engine, first.id)
    two = mark_offer_sent(engine, second.id)

    assert one.company_id == existing.id == two.company_id
    with Session(engine) as session:
        assert len(list(session.scalars(select(Company)))) == 1
    with pytest.raises(OfferAlreadySent):
        mark_offer_sent(engine, first.id)


def test_mark_sent_handles_missing_company_name_and_unknown_offer(engine: Engine) -> None:
    offer = create_offer(engine, _offer("Développeur PHP", "PHP.", company="", location=""))

    application = mark_offer_sent(engine, offer.id)

    with Session(engine) as session:
        company = session.get(Company, application.company_id)
        assert company is not None
        assert company.name == "Entreprise non précisée"
        assert company.location == "Lieu non précisé"
    with pytest.raises(OfferNotFound):
        mark_offer_sent(engine, "0" * 32)


def test_set_offer_status_ignores_offer_from_queue(engine: Engine) -> None:
    offer = create_offer(engine, _offer("Développeur PHP Symfony", "PHP et Symfony."))

    set_offer_status(engine, offer.id, "Écartée")

    assert build_send_queue(engine, _profile()) == []
    with pytest.raises(OfferNotFound):
        set_offer_status(engine, "0" * 32, "Écartée")


def test_due_follow_ups_and_postponement(engine: Engine) -> None:
    offer = create_offer(engine, _offer("Développeur PHP", "PHP."))
    application = mark_offer_sent(engine, offer.id, sent_on=date(2026, 10, 1))

    assert due_follow_ups(engine, today=date(2026, 10, 7)) == []
    [due] = due_follow_ups(engine, today=date(2026, 10, 8))
    assert due.id == application.id

    updated = mark_followed_up(engine, application.id, today=date(2026, 10, 8))

    assert updated.next_action_on == "2026-10-15"
    assert "Relancée le 2026-10-08." in updated.notes
    assert due_follow_ups(engine, today=date(2026, 10, 8)) == []
    with pytest.raises(ApplicationNotFound):
        mark_followed_up(engine, "0" * 32)


def _prospect(engine: Engine, name: str, location: str = "Valbonne") -> Company:
    return create_company(
        engine,
        CompanyData(
            name=name,
            location=location,
            source_url="https://annuaire-entreprises.data.gouv.fr/entreprise/315000943",
            development_evidence="Estimation La Bonne Boîte.",
        ),
    )


def test_spontaneous_queue_lists_prospects_without_any_application(engine: Engine) -> None:
    fresh = _prospect(engine, "Alpha Info")
    with_letter = _prospect(engine, "Beta Soft")
    applied_spontaneously = _prospect(engine, "Gamma Dev")
    applied_via_offer = _prospect(engine, "Atelier Tech", "Nice")
    save_cover_letter(engine, "Madame, Monsieur,\n\nBonjour.", company_id=with_letter.id)
    mark_spontaneous_sent(engine, applied_spontaneously.id, role="Développeur PHP")
    offer = create_offer(engine, _offer("Développeur PHP", "PHP.", company="Atelier Tech"))
    mark_offer_sent(engine, offer.id)

    queue = build_spontaneous_queue(engine)

    by_name = {item.company.name: item for item in queue}
    assert set(by_name) == {"Alpha Info", "Beta Soft"}
    assert by_name["Alpha Info"].has_letter is False
    assert by_name["Beta Soft"].has_letter is True
    assert fresh.id == by_name["Alpha Info"].company.id
    assert applied_via_offer.id not in {item.company.id for item in queue}


def test_mark_spontaneous_sent_creates_application_without_offer(engine: Engine) -> None:
    company = _prospect(engine, "Alpha Info")

    application = mark_spontaneous_sent(
        engine, company.id, role="  Développeur backend  ", sent_on=date(2026, 10, 6)
    )

    assert application.job_offer_id is None
    assert application.role == "Développeur backend"
    assert application.status == "Envoyée"
    assert application.next_action_on == "2026-10-13"
    assert application.notes == "Candidature spontanée."
    assert [a.id for a in list_applications(engine)] == [application.id]
    assert [a.id for a in due_follow_ups(engine, today=date(2026, 10, 13))] == [application.id]
    with pytest.raises(SpontaneousAlreadySent):
        mark_spontaneous_sent(engine, company.id, role="Développeur backend")


def test_mark_spontaneous_sent_validates_company_and_role(engine: Engine) -> None:
    company = _prospect(engine, "Alpha Info")

    with pytest.raises(ValueError, match="poste visé"):
        mark_spontaneous_sent(engine, company.id, role="   ")
    with pytest.raises(CompanyNotFound):
        mark_spontaneous_sent(engine, "0" * 32, role="Développeur")


def test_contact_search_links_are_plain_links_with_encoded_company_name(engine: Engine) -> None:
    company = _prospect(engine, "Société & Fils", "Aix-en-Provence")

    links = dict(contact_search_links(company))

    assert links["Chercher le site et le contact"] == (
        "https://www.google.com/search?q=Soci%C3%A9t%C3%A9+%26+Fils+Aix-en-Provence+contact+recrutement"
    )
    assert links["Chercher sur LinkedIn"].endswith("keywords=Soci%C3%A9t%C3%A9+%26+Fils")


def _add_reference_resume(engine: Engine) -> None:
    with Session(engine) as session, session.begin():
        session.add(
            ResumeVersion(
                id="a" * 32,
                original_filename="cv.pdf",
                stored_filename="cv-stored.pdf",
                reviewed_text="Développeur PHP Symfony.",
                created_at="2026-01-01T00:00:00+00:00",
            )
        )


def _truncated_offer(engine: Engine) -> JobOffer:
    excerpt = ("Développeur backend. " + "Missions variées au sein de l'équipe. " * 20)[:500]
    return create_offer(
        engine,
        _offer("Développeur backend", excerpt, source="Adzuna (FR)"),
    )


FULL_TEXT = (
    "Développeur backend PHP Symfony. PHP et Symfony sont requis, avec SQL et Docker. "
    "Vous concevez des API REST pour notre plateforme. " * 6
)


def test_truncated_detection_targets_adzuna_excerpts_only(engine: Engine) -> None:
    truncated = _truncated_offer(engine)
    full = create_offer(
        engine,
        _offer("Développeur PHP", "PHP. " * 200, source="Adzuna (FR)", url="https://e.com/2"),
    )
    short = create_offer(
        engine,
        _offer("Développeur Symfony", "Symfony.", source="Adzuna (FR)", url="https://e.com/3"),
    )
    other_source = create_offer(
        engine,
        _offer("Développeur Go", "x" * 500, source="Greenhouse", url="https://e.com/4"),
    )

    assert len(truncated.description) == 500
    assert is_probably_truncated(truncated) is True
    assert is_probably_truncated(full) is False
    assert is_probably_truncated(short) is False
    assert is_probably_truncated(other_source) is False


def test_complete_description_updates_offer_score_and_regenerates_dossier(
    engine: Engine,
) -> None:
    _add_reference_resume(engine)
    offer = _truncated_offer(engine)

    update = complete_offer_description(engine, offer.id, f"  {FULL_TEXT}  ", _profile())

    assert update.score_after > update.score_before
    assert update.dossier_regenerated is True
    assert update.dossier_error is None
    [stored] = list_offers(engine)
    assert stored.description == FULL_TEXT.strip()
    assert is_probably_truncated(stored) is False
    assert get_tailored_resume(engine, offer.id) is not None
    assert get_cover_letter(engine, job_offer_id=offer.id) is not None


def test_complete_description_can_skip_regeneration_to_keep_manual_edits(
    engine: Engine,
) -> None:
    _add_reference_resume(engine)
    offer = _truncated_offer(engine)
    save_cover_letter(engine, "Ma lettre retravaillée à la main.", job_offer_id=offer.id)

    update = complete_offer_description(engine, offer.id, FULL_TEXT, _profile(), regenerate=False)

    assert update.dossier_regenerated is False
    assert get_cover_letter(engine, job_offer_id=offer.id).content == (
        "Ma lettre retravaillée à la main."
    )
    assert get_tailored_resume(engine, offer.id) is None


def test_complete_description_reports_missing_reference_resume(engine: Engine) -> None:
    offer = _truncated_offer(engine)

    update = complete_offer_description(engine, offer.id, FULL_TEXT, _profile())

    assert update.dossier_regenerated is False
    assert "Aucun CV de référence" in (update.dossier_error or "")
    assert list_offers(engine)[0].description == FULL_TEXT.strip()


def test_complete_description_rejects_useless_text_and_unknown_offer(engine: Engine) -> None:
    offer = _truncated_offer(engine)

    with pytest.raises(ValueError, match="texte complet"):
        complete_offer_description(engine, offer.id, "   ", _profile())
    with pytest.raises(ValueError, match="pas plus complet"):
        complete_offer_description(engine, offer.id, "Trop court.", _profile())
    with pytest.raises(ValueError, match="taille maximale"):
        complete_offer_description(engine, offer.id, "x" * 100_001, _profile())
    with pytest.raises(OfferNotFound):
        complete_offer_description(engine, "0" * 32, FULL_TEXT, _profile())
    assert len(list_offers(engine)[0].description) == 500


def test_follow_ups_ignore_applications_that_are_not_pending(engine: Engine) -> None:
    offer = create_offer(engine, _offer("Développeur PHP", "PHP."))
    application = mark_offer_sent(engine, offer.id, sent_on=date(2026, 10, 1))
    with Session(engine) as session, session.begin():
        stored = session.get(Application, application.id)
        assert stored is not None
        stored.status = "Entretien"

    assert due_follow_ups(engine, today=date(2026, 12, 1)) == []


def test_prep_seconds_are_stored_and_averaged(engine: Engine) -> None:
    assert average_prep_seconds(engine) is None
    first = create_offer(engine, _offer("Développeur PHP", "PHP.", url="https://example.com/1"))
    second = create_offer(engine, _offer("Développeur Symfony", "Symfony.", url="https://example.com/2"))
    third = create_offer(engine, _offer("Développeur Laravel", "Laravel.", url="https://example.com/3"))

    mark_offer_sent(engine, first.id, prep_seconds=120)
    mark_offer_sent(engine, second.id, prep_seconds=240)
    mark_offer_sent(engine, third.id)

    assert average_prep_seconds(engine) == 180


def test_clamp_prep_seconds_bounds_forgotten_windows() -> None:
    assert clamp_prep_seconds(-5) == 0
    assert clamp_prep_seconds(95.7) == 95
    assert clamp_prep_seconds(10 * 3600) == 3600
