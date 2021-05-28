import json
import sys
from typing import Any, Dict, List, Optional
from uuid import UUID

from fastapi import FastAPI, HTTPException

from api.models import (
    BSNRetrievalToken,
    DomesticGreenCard,
    DomesticStaticQrResponse,
    EUGreenCard,
    MobileAppProofOfVaccination,
    PaperProofOfVaccination,
    PrepareIssueMessage,
    StatementOfVaccination,
    StepTwoData,
    UnomiEventToken,
)
from api.requesters import mobile_app_step_1
from api.requesters.mobile_app_prepare_issue import get_prepare_issue
from api.session_store import session_store
from api.signers import eu_international, nl_domestic_dynamic, nl_domestic_static

app = FastAPI()


@app.get("/")
@app.get("/health")
async def health() -> Dict[str, Any]:
    redis_health = session_store.health_check()
    return {
        "running": True,
        "service_status": {
            # this is probably not really needed as it's also monitored at ops(!)
            # todo: It's good to know what services to expect working, should be added to the readme.
            "sbv-z": "todo",
            "inge6": "todo",
            "eu-signer": "todo",
            "domestic-signer": "todo",
            "rvig": "todo",
            "redis": redis_health,
        },
    }


@app.post("/app/access_tokens/", response_model=List[UnomiEventToken])
async def sign_via_app_step_1(request: BSNRetrievalToken) -> List[UnomiEventToken]:
    """
    Creates unomi events based on DigiD BSN retrieval token.
    .. image:: ./docs/sequence-diagram-unomi-events.png

    :param request: BSNRetrievalToken
    :return:
    """
    bsn = await mobile_app_step_1.get_bsn_from_inge6(request)
    return mobile_app_step_1.identity_provider_calls(bsn)


@app.post("/app/paper/", response_model=PaperProofOfVaccination)
async def sign_via_inge3(data: StatementOfVaccination):
    # todo: bring in line with dynamic signing
    domestic_response: Optional[List[DomesticStaticQrResponse]] = nl_domestic_static.sign(data)
    eu_response = eu_international.sign(data)
    return PaperProofOfVaccination(**{"domesticProof": domestic_response, "euProofs": eu_response})


@app.post("/app/prepare_issue/", response_model=PrepareIssueMessage)
async def app_prepare_issue():
    return await get_prepare_issue()


@app.post("/app/sign/", response_model=MobileAppProofOfVaccination)
async def sign_via_app_step_2(data: StepTwoData):
    # todo: check CMS signature (where are those in the message?)

    # Check session: no issue message stored under given stoken, no session
    prepare_issue_message = step_2_get_issue_message(data.stoken)
    if not prepare_issue_message:
        raise HTTPException(status_code=401, detail=["Invalid session"])

    domestic_response: Optional[DomesticGreenCard] = nl_domestic_dynamic.sign(data, prepare_issue_message)
    eu_response: Optional[List[EUGreenCard]] = eu_international.sign(data.events)

    return MobileAppProofOfVaccination(**{"domesticGreencard": domestic_response, "euGreencards": eu_response})


def step_2_get_issue_message(stoken: UUID) -> Optional[str]:
    # Explicitly do not push the prepare_issue_message into a model: the structure will change over time
    # and that change has to be transparent.
    # Pydantic validates the stoken into a uuid, but the redis code needs a string.
    prepare_issue_message = session_store.get_message(str(stoken))
    return prepare_issue_message.decode("UTF-8") if prepare_issue_message else None


def save_openapi_json():
    # Helper function to render the latest open API spec to the docs directory.
    with open("docs/openapi.json", "w") as file:
        json.dump(app.openapi(), file)
    sys.exit()
