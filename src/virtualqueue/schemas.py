import re
from datetime import date
from typing import Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, field_validator


class Input(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class Demographics(Input):
    birth_date: date
    postcode: str = Field(min_length=5, max_length=10)
    family_name: str = Field(min_length=1, max_length=100)

    @field_validator("birth_date")
    @classmethod
    def plausible_birth(cls, value):
        today = date.today()
        if not date(1900, 1, 1) <= value <= today:
            raise ValueError("Enter a valid date of birth")
        return value

    @field_validator("postcode")
    @classmethod
    def postcode_format(cls, value):
        value = re.sub(r"\s", "", value.upper())
        if not re.fullmatch(r"[A-Z]{1,2}[0-9][A-Z0-9]?[0-9][A-Z]{2}", value):
            raise ValueError("Enter a full UK postcode")
        return value[:-3] + " " + value[-3:]


class PatientStart(Demographics):
    service_id: str = Field(min_length=1, max_length=80)
    name: str = Field(min_length=2, max_length=100)
    phone: str = Field(min_length=10, max_length=20)
    concern: str = Field(default="", max_length=1000)
    existing_reference: str | None = Field(default=None, max_length=36)
    consent_to_contact: Literal[True]
    emergency: bool

    @field_validator("phone")
    @classmethod
    def phone_format(cls, value):
        value = value.replace(" ", "")
        if not re.fullmatch(r"\+[1-9][0-9]{8,14}", value):
            raise ValueError("Use international format, for example +447700900123")
        return value


class VerifyCode(Input):
    challenge_id: str
    code: str = Field(pattern=r"^[0-9]{6}$")


class Versioned(Input):
    version: int = Field(ge=1)


class Assessment(Versioned):
    decision: Literal["remote_wait", "attend_now"]
    priority: int = Field(ge=1, le=5)
    assessment_reference: str = Field(min_length=3, max_length=150)
    identity_confirmed: Literal[True]
    phone_assessment_completed: Literal[True]
    safety_net: str = Field(min_length=20, max_length=2000)
    notes: str = Field(min_length=10, max_length=4000)
    review_due: AwareDatetime
    approved_until: AwareDatetime
    slot_id: str | None = None


class Transition(Versioned):
    action: Literal["start_assessment", "arrived", "start_care", "complete", "close_after_review", "recall", "cancel_booking"]
    reason: str = Field(min_length=5, max_length=1000)


class Journey(Input):
    minutes: int = Field(ge=1, le=240)
    buffer_minutes: int = Field(default=15, ge=5, le=90)
    mode: Literal["lift", "taxi", "public_transport", "walking", "ambulance"] = "lift"


class RouteRequest(Input):
    latitude: float = Field(ge=-90, le=90)
    longitude: float = Field(ge=-180, le=180)
    mode: Literal["lift", "taxi", "public_transport", "walking"]
    location_consent: Literal[True]


class SlotInput(Input):
    external_id: str = Field(min_length=1, max_length=150)
    start: AwareDatetime
    end: AwareDatetime
    capacity: int = Field(ge=0, le=200)
    enabled: bool = True


class Feed(Input):
    version: int = Field(ge=1)
    observed_at: AwareDatetime
    paused: bool = False
    slots: list[SlotInput] = Field(max_length=500)


class ServiceInput(Input):
    id: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{1,79}$")
    name: str = Field(min_length=3, max_length=160)
    address: str = Field(min_length=5, max_length=300)
    phone: str = Field(min_length=5, max_length=30)
    latitude: float = Field(ge=-90, le=90)
    longitude: float = Field(ge=-180, le=180)
    callback_minutes: int = Field(default=15, ge=1, le=120)


class AssistedRequest(Demographics):
    name: str = Field(min_length=2, max_length=100)
    phone: str = Field(min_length=10, max_length=20)
    concern: str = Field(min_length=3, max_length=1000)
    source: Literal["telephone", "ambulance", "nhs111", "walk_in"]
    external_reference: str = Field(min_length=3, max_length=150)
    requires_immediate_assessment: bool = False

    @field_validator("phone")
    @classmethod
    def phone_format(cls, value):
        return PatientStart.phone_format(value)


class DemographicMatch(Versioned):
    birth_date: date
    postcode: str
    family_name: str = Field(min_length=1, max_length=100)
    status: Literal["matched", "manual_review"]
    nhs_number: str | None = None
    local_record_reference: str | None = Field(default=None, max_length=150)
    source_reference: str = Field(min_length=3, max_length=150)


class Reconcile(Input):
    accepted: bool
    external_reference: str = Field(min_length=3, max_length=150)
    reason: str = Field(min_length=10, max_length=1000)
