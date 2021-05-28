import copy
import logging
from datetime import datetime, timedelta
from typing import List

import pytz

from api.models import EUGreenCard, MessageToEUSigner, StatementOfVaccination
from api.settings import settings
from api.utils import request_post_with_retries

log = logging.getLogger(__package__)


def sign(statement: StatementOfVaccination) -> List[EUGreenCard]:
    # git clone https://github.com/minvws/nl-covid19-coronacheck-hcert-private

    # https://github.com/eu-digital-green-certificates/dgc-testdata/blob/main/NL/2DCode/raw/100.json
    # https://github.com/ehn-digital-green-development/ehn-dgc-schema/blob/main/DGC.combined-schema.json

    """
    EU only has one event per signing request. This means that a statement of vaccination has to be broken down
    into the maximum of three different types of requests. Each type of event can only be sent once.

    If you have multiple recoveries, tests or vaccinations just use one of each.

    Todo: the dates of what events are chosen might/will impact someone. It's a political choice what has preference.

    We now use:
    - the latest test, as that may have expired.
    - the oldest vaccination: as vaccinations are valid for a long time and it takes time before a vaccination 'works'
    - the oldest recovery
    """
    blank_statement = copy.deepcopy(statement)
    blank_statement.events = []

    # todo; it's unclear what expiration time is. This is just a mock implementation.
    expiration_time = datetime.now(pytz.utc) + timedelta(days=180)

    statements_to_eu_signer = []
    # EventTime: vaccination: dt, test: sc, recovery: fr
    # Get the first item from the list and perform a type check for mypy as vaccination is nested.
    if statement.vaccinations:
        blank_statement.events = [statement.vaccinations[0]]
        statements_to_eu_signer.append(
            MessageToEUSigner(
                **{
                    "keyUsage": "vaccination",
                    # Todo: It's unclear what experiation time was. It used to be eventtime.
                    # "eventTime": blank_statement.vaccinations[0].data.date,
                    "expirationTime": expiration_time,
                    "dgc": blank_statement.toEuropeanOnlineSigningRequest(),
                }
            )
        )

    if statement.recoveries:
        blank_statement.events = [statement.recoveries[-1]]
        statements_to_eu_signer.append(
            MessageToEUSigner(
                **{
                    "keyUsage": "recovery",
                    # "eventTime": blank_statement.recoveries[0].data.sampleDate,
                    "expirationTime": expiration_time,
                    "dgc": blank_statement.toEuropeanOnlineSigningRequest(),
                }
            )
        )

    if statement.tests:
        blank_statement.events = [statement.tests[-1]]
        statements_to_eu_signer.append(
            MessageToEUSigner(
                **{
                    "keyUsage": "test",
                    # todo: EventTime is gone, ExpirationTime is added, what is ExpirationTime?
                    # "eventTime": blank_statement.tests[0].data.sampleDate,
                    "expirationTime": expiration_time,
                    "dgc": blank_statement.toEuropeanOnlineSigningRequest(),
                }
            )
        )

    greencards = []
    for statement_to_eu_signer in statements_to_eu_signer:
        response = request_post_with_retries(
            settings.EU_INTERNATIONAL_SIGNING_URL,
            # by_alias uses the alias field to create a json object. As such 'is_' will be 'is'.
            data=statement_to_eu_signer.dict(by_alias=True),
            headers={"accept": "application/json", "Content-Type": "application/json"},
        )
        if response.status_code != 200:
            log.error(response.content)
        response.raise_for_status()
        data = response.json()
        origins = [
            {
                "type": statement_to_eu_signer.keyUsage,
                "eventTime": str(get_event_time(statement_to_eu_signer).isoformat()),
                "expirationTime": str(expiration_time.isoformat()),
                "validFrom": str(get_event_time(statement_to_eu_signer).isoformat()),
            }
        ]
        greencards.append(EUGreenCard(**{**data, **{"origins": origins}}))
    return greencards


def get_event_time(statement_to_eu_signer: MessageToEUSigner):
    if statement_to_eu_signer.keyUsage == "vaccination":
        return statement_to_eu_signer.dgc.v[0].dt
    if statement_to_eu_signer.keyUsage == "recovery":
        return statement_to_eu_signer.dgc.r[0].fr
    if statement_to_eu_signer.keyUsage == "test":
        return statement_to_eu_signer.dgc.t[0].sc

    raise ValueError("Not able to retrieve an event time from the statement to the signer. This is very wrong.")
