from typing import List, Optional
from pydantic import BaseModel
from fastapi import APIRouter, Depends, HTTPException, status, Request
import logging

from open_webui.models.knowledge import (
    Knowledges,
    KnowledgeForm,
    KnowledgeResponse,
    KnowledgeUserResponse,
    KnowledgeChunksModel
)
from open_webui.models.files import Files, FileModel, FileMetadataResponse
from open_webui.retrieval.vector.factory import VECTOR_DB_CLIENT
from open_webui.routers.retrieval import (
    process_file,
    ProcessFileForm,
    process_files_batch,
    BatchProcessFilesForm,
)
from open_webui.storage.provider import Storage

from open_webui.constants import ERROR_MESSAGES
from open_webui.utils.auth import get_verified_user
from open_webui.utils.access_control import has_access, has_permission


from open_webui.env import SRC_LOG_LEVELS
from open_webui.config import BYPASS_ADMIN_ACCESS_CONTROL
from open_webui.models.models import Models, ModelForm


log = logging.getLogger(__name__)
log.setLevel(SRC_LOG_LEVELS["MODELS"])

router = APIRouter()

############################
# getKnowledgeBases
############################


@router.get("/", response_model=list[KnowledgeUserResponse])
async def get_knowledge(user=Depends(get_verified_user)):
    knowledge_bases = []

    if user.role == "admin" and BYPASS_ADMIN_ACCESS_CONTROL:
        knowledge_bases = Knowledges.get_knowledge_bases()
    else:
        knowledge_bases = Knowledges.get_knowledge_bases_by_user_id(user.id, "read")

    # Get files for each knowledge base
    knowledge_with_files = []
    for knowledge_base in knowledge_bases:
        files = []
        if knowledge_base.data:
            files = Files.get_file_metadatas_by_ids(
                knowledge_base.data.get("file_ids", [])
            )

            # Check if all files exist
            if len(files) != len(knowledge_base.data.get("file_ids", [])):
                missing_files = list(
                    set(knowledge_base.data.get("file_ids", []))
                    - set([file.id for file in files])
                )
                if missing_files:
                    data = knowledge_base.data or {}
                    file_ids = data.get("file_ids", [])

                    for missing_file in missing_files:
                        file_ids.remove(missing_file)

                    data["file_ids"] = file_ids
                    Knowledges.update_knowledge_data_by_id(
                        id=knowledge_base.id, data=data
                    )

                    files = Files.get_file_metadatas_by_ids(file_ids)

        knowledge_with_files.append(
            KnowledgeUserResponse(
                **knowledge_base.model_dump(),
                files=files,
            )
        )

    return knowledge_with_files


@router.get("/list", response_model=list[KnowledgeUserResponse])
async def get_knowledge_list(user=Depends(get_verified_user)):
    knowledge_bases = []

    if user.role == "admin" and BYPASS_ADMIN_ACCESS_CONTROL:
        knowledge_bases = Knowledges.get_knowledge_bases()
    else:
        knowledge_bases = Knowledges.get_knowledge_bases_by_user_id(user.id, "write")

    # Get files for each knowledge base
    knowledge_with_files = []
    for knowledge_base in knowledge_bases:
        files = []
        if knowledge_base.data:
            files = Files.get_file_metadatas_by_ids(
                knowledge_base.data.get("file_ids", [])
            )

            # Check if all files exist
            if len(files) != len(knowledge_base.data.get("file_ids", [])):
                missing_files = list(
                    set(knowledge_base.data.get("file_ids", []))
                    - set([file.id for file in files])
                )
                if missing_files:
                    data = knowledge_base.data or {}
                    file_ids = data.get("file_ids", [])

                    for missing_file in missing_files:
                        file_ids.remove(missing_file)

                    data["file_ids"] = file_ids
                    Knowledges.update_knowledge_data_by_id(
                        id=knowledge_base.id, data=data
                    )

                    files = Files.get_file_metadatas_by_ids(file_ids)

        knowledge_with_files.append(
            KnowledgeUserResponse(
                **knowledge_base.model_dump(),
                files=files,
            )
        )
    return knowledge_with_files


############################
# CreateNewKnowledge
############################


@router.post("/create", response_model=Optional[KnowledgeResponse])
async def create_new_knowledge(
    request: Request, form_data: KnowledgeForm, user=Depends(get_verified_user)
):
    if user.role != "admin" and not has_permission(
        user.id, "workspace.knowledge", request.app.state.config.USER_PERMISSIONS
    ):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=ERROR_MESSAGES.UNAUTHORIZED,
        )

    knowledge = Knowledges.insert_new_knowledge(user.id, form_data)

    if knowledge:
        return knowledge
    else:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=ERROR_MESSAGES.FILE_EXISTS,
        )


############################
# ReindexKnowledgeFiles
############################


@router.post("/reindex", response_model=bool)
async def reindex_knowledge_files(request: Request, user=Depends(get_verified_user)):
    if user.role != "admin":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=ERROR_MESSAGES.UNAUTHORIZED,
        )

    knowledge_bases = Knowledges.get_knowledge_bases()

    log.info(f"Starting reindexing for {len(knowledge_bases)} knowledge bases")

    deleted_knowledge_bases = []

    for knowledge_base in knowledge_bases:
        # -- Robust error handling for missing or invalid data
        if not knowledge_base.data or not isinstance(knowledge_base.data, dict):
            log.warning(
                f"Knowledge base {knowledge_base.id} has no data or invalid data ({knowledge_base.data!r}). Deleting."
            )
            try:
                Knowledges.delete_knowledge_by_id(id=knowledge_base.id)
                deleted_knowledge_bases.append(knowledge_base.id)
            except Exception as e:
                log.error(
                    f"Failed to delete invalid knowledge base {knowledge_base.id}: {e}"
                )
            continue

        try:
            file_ids = knowledge_base.data.get("file_ids", [])
            files = Files.get_files_by_ids(file_ids)
            try:
                if VECTOR_DB_CLIENT.has_collection(collection_name=knowledge_base.id):
                    VECTOR_DB_CLIENT.delete_collection(
                        collection_name=knowledge_base.id
                    )
            except Exception as e:
                log.error(f"Error deleting collection {knowledge_base.id}: {str(e)}")
                continue  # Skip, don't raise

            failed_files = []
            for file in files:
                try:
                    process_file(
                        request,
                        ProcessFileForm(
                            file_id=file.id, collection_name=knowledge_base.id
                        ),
                        user=user,
                    )
                except Exception as e:
                    log.error(
                        f"Error processing file {file.filename} (ID: {file.id}): {str(e)}"
                    )
                    failed_files.append({"file_id": file.id, "error": str(e)})
                    continue

        except Exception as e:
            log.error(f"Error processing knowledge base {knowledge_base.id}: {str(e)}")
            # Don't raise, just continue
            continue

        if failed_files:
            log.warning(
                f"Failed to process {len(failed_files)} files in knowledge base {knowledge_base.id}"
            )
            for failed in failed_files:
                log.warning(f"File ID: {failed['file_id']}, Error: {failed['error']}")

    log.info(
        f"Reindexing completed. Deleted {len(deleted_knowledge_bases)} invalid knowledge bases: {deleted_knowledge_bases}"
    )
    return True


############################
# GetKnowledgeById
############################


class KnowledgeFilesResponse(KnowledgeResponse):
    files: list[FileMetadataResponse]


@router.get("/{id}", response_model=Optional[KnowledgeFilesResponse])
async def get_knowledge_by_id(id: str, user=Depends(get_verified_user)):
    knowledge = Knowledges.get_knowledge_by_id(id=id)

    if knowledge:

        if (
            user.role == "admin"
            or knowledge.user_id == user.id
            or has_access(user.id, "read", knowledge.access_control)
        ):

            file_ids = knowledge.data.get("file_ids", []) if knowledge.data else []
            files = Files.get_file_metadatas_by_ids(file_ids)

            return KnowledgeFilesResponse(
                **knowledge.model_dump(),
                files=files,
            )
    else:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=ERROR_MESSAGES.NOT_FOUND,
        )
    

############################
# Get Knowledge Chunks By Id
############################


@router.get("/{id}/{knowledgeId}/chunks", response_model=Optional[KnowledgeChunksModel])
async def get_file_chunks_by_id(id: str, knowledgeId: str, user=Depends(get_verified_user)):
    file_obj = Knowledges.get_knowledge_by_id(id=knowledgeId)
    log.debug(file_obj)
    # file_obj = VECTOR_DB_CLIENT.query(
    #     collection_name=knowledgeId, 
    #     filter={"file_id": id}
    # )
    # file_obj = VECTOR_DB_CLIENT.search(
    #     collection_name="file-29b8c427-71b0-49a5-bb73-e7b9fa6807b9",
    #     vectors = [[-0.0627717599272728,0.05495879799127579,0.05216481164097786,0.0857900083065033,-0.0827488973736763,-0.07457300275564194,0.06855475157499313,0.018396412953734398,-0.08201129734516144,-0.03738484904170036,0.012124928645789623,0.003518295008689165,-0.004134328570216894,-0.04378440976142883,0.021807309240102768,-0.005102778784930706,0.01954658329486847,-0.04234873875975609,-0.11035963147878647,0.005424534901976585,-0.05573483929038048,0.028052449226379395,-0.023158704861998558,0.028481367975473404,-0.05370963737368584,-0.05260159820318222,0.03393926844000816,0.045388661324977875,0.02371845953166485,-0.07312081009149551,0.05477771908044815,0.017047269269824028,0.08136038482189178,-0.002862718654796481,0.011958064511418343,0.07355856150388718,-0.09423741698265076,-0.0813620314002037,0.040015339851379395,0.0006922144093550742,-0.013393286615610123,-0.05453811585903168,0.00515141012147069,-0.02613980323076248,0.036807116121053696,-0.033959634602069855,0.021093245595693588,0.055948879569768906,0.05778136104345322,-0.005418389569967985,-0.06841320544481277,-0.09023692458868027,-0.04286674037575722,0.023652728646993637,0.12149357795715332,0.032392874360084534,-0.0226594265550375,-0.02328171581029892,0.048865992575883865,-0.059351593255996704,-0.03406606242060661,0.035962607711553574,-0.08115774393081665,-0.02197944186627865,0.01318791601806879,-0.04585551843047142,-0.0704198032617569,-0.052603255957365036,-0.04802922159433365,-0.07454615086317062,-0.028731800615787506,0.012831898406147957,-0.04292174056172371,0.005366343539208174,-0.03909757360816002,0.015919279307127,0.02024351805448532,0.005266707390546799,0.009581310674548149,-0.004767658654600382,0.048087675124406815,-0.07309658825397491,-0.05034445971250534,0.009653022512793541,0.013496565632522106,0.00043039166484959424,0.019725175574421883,0.06249547377228737,-0.018227215856313705,0.031097158789634705,-0.08885389566421509,0.04813247174024582,0.026074260473251343,-0.011494044214487076,-0.09936173260211945,-0.027338193729519844,0.07621534913778305,-0.002096568001434207,-0.12358879297971725,0.2980653643608093,0.05630643665790558,0.07848011702299118,0.011043567210435867,0.05240008234977722,-0.001188015565276146,0.0009656775509938598,-0.054327283054590225,0.026764750480651855,-0.012194271199405193,0.01071705762296915,-3.70864181604702e-05,-0.03556566685438156,-0.032085612416267395,0.016168808564543724,0.08674973249435425,0.018139397725462914,-0.011017575860023499,0.04974033683538437,0.022581027820706367,-2.236113323306199e-05,0.014939038082957268,-0.0111762136220932,0.005659330636262894,-0.004114771261811256,0.0031826291233301163,0.023865047842264175,0.01805102825164795,-5.850476526344804e-33,0.056107040494680405,-0.03449847921729088,0.0331118069589138,0.1677229106426239,-0.03103817068040371,-0.004835353698581457,-0.061064984649419785,-0.06271098554134369,0.02742657996714115,0.06364016234874725,0.043340492993593216,0.06082936003804207,-0.01816082000732422,0.04280414059758186,0.01902409829199314,0.087977834045887,-0.03913283720612526,0.04414263367652893,-0.004941101651638746,0.051052726805210114,-0.05431908741593361,0.01117307972162962,0.026699043810367584,0.07509665191173553,0.048690181225538254,-0.04354275017976761,0.013338671997189522,-0.10262981802225113,0.05215670168399811,0.02223236858844757,-0.02974178083240986,-0.04227067157626152,0.02298460155725479,0.039574410766363144,0.009094753302633762,0.020864710211753845,0.005092637147754431,-0.06276412308216095,-0.0502176508307457,-0.005225035361945629,-0.05349821597337723,0.029773490503430367,0.021753674373030663,-0.020678050816059113,0.02386615052819252,0.005806648172438145,-0.0012137857265770435,0.02266031503677368,0.003156717400997877,0.030960390344262123,-0.05296811833977699,0.01867692545056343,-0.1401534080505371,0.04147821664810181,-0.010278232395648956,-0.011593111790716648,-0.033458270132541656,-0.05058896914124489,0.04686778783798218,0.024715082719922066,0.033496879041194916,0.11170760542154312,-0.04034861922264099,-0.004284453112632036,-0.08074362576007843,-0.05611220374703407,0.03833863139152527,0.011508344672620296,0.06873798370361328,-0.03811737895011902,-0.04598831757903099,-0.016439612954854965,0.024412987753748894,0.01172209344804287,0.00816850084811449,0.03903692215681076,0.026186516508460045,0.010481818579137325,0.04286015033721924,-0.046381544321775436,0.006481113377958536,0.044423408806324005,-0.01883763074874878,0.007263376843184233,0.056179553270339966,0.053477950394153595,-0.02148381993174553,-0.08448575437068939,-0.012813943438231945,-0.03947554901242256,-0.05802653357386589,0.03133397176861763,0.04541732743382454,0.011839093640446663,-0.01790633611381054,4.58621618898811e-33,0.13150791823863983,0.07932809740304947,-0.09495951235294342,-0.02447219006717205,-0.05581621080636978,-0.009145251475274563,-0.032090429216623306,0.11379317939281464,-0.1444162130355835,0.008509622886776924,0.030667012557387352,-0.012414908036589622,0.07017054408788681,0.028179822489619255,0.040822092443704605,0.0195908285677433,0.14295043051242828,0.056869987398386,-0.04012206569314003,-0.017843887209892273,-0.06138448044657707,0.0008760813507251441,-0.0549062080681324,-0.007102477829903364,-0.0004941553343087435,-0.013119391165673733,-0.002266978146508336,0.058566685765981674,-0.09971514344215393,-0.025532398372888565,0.07856462150812149,0.020517664030194283,-0.0046333917416632175,0.030302628874778748,0.016833264380693436,0.09143756330013275,0.01676974631845951,-0.07980289310216904,0.04251030460000038,-0.0843973457813263,-0.023859089240431786,0.04701947793364525,0.002414868911728263,0.1093178242444992,-0.03390536084771156,-0.06402090191841125,-0.03772387653589249,0.029074793681502342,-0.04244937747716904,0.01586185209453106,-0.09060660004615784,-0.05576632544398308,0.022608736529946327,0.008000320754945278,-0.022470008581876755,0.022058162838220596,-0.025199105963110924,0.030339548364281654,0.010110803879797459,-0.01854739338159561,0.016989536583423615,0.0763101726770401,0.0416206493973732,0.08752063661813736,-0.012092780321836472,0.031135160475969315,-0.03219039738178253,0.012797964736819267,0.013538787141442299,-0.029752414673566818,0.03678986802697182,-0.005846083164215088,-0.01105550304055214,0.038670312613248825,-0.020741449669003487,-0.011265089735388756,-0.023767538368701935,-0.009984567761421204,-0.023108452558517456,0.012011340819299221,-0.01064456719905138,0.0513605996966362,-0.027734095230698586,-0.00021139727323316038,0.0009463068563491106,-0.03158263489603996,0.05122927203774452,0.044328588992357254,-0.00377340754494071,-0.04166809096932411,0.028954582288861275,0.03306134417653084,-0.015184316784143448,-0.00015764549607411027,-0.044124405831098557,-1.480350064753111e-08,-0.008695975877344608,0.00013011027476750314,0.01647111214697361,0.05922887101769447,0.0455387681722641,0.03312689810991287,-0.09335853904485703,-0.039027195423841476,-0.0206561591476202,0.012607509270310402,0.06953947246074677,0.07919185608625412,-0.07191091030836105,-0.004785140510648489,0.08800482749938965,0.04758850485086441,-0.05219272896647453,-0.007525491062551737,-0.057711806148290634,-0.09295842796564102,-0.004501511808484793,0.0011780671775341034,0.024447282776236534,-0.06403963267803192,-0.003227506298571825,-0.027965422719717026,-0.035407572984695435,0.025036703795194626,-0.009852781891822815,0.013252449221909046,0.00113848433829844,0.17805427312850952,-0.036146312952041626,-0.007625873200595379,-0.03220124542713165,-0.04229322075843811,0.0047745127230882645,0.028531795367598534,0.07472303509712219,-0.014894162304699421,-0.05622071027755737,0.02723585069179535,-0.011199424974620342,-0.10166790336370468,-0.019529249519109726,0.027246464043855667,0.035081107169389725,-0.08160565793514252,-0.0013378537259995937,-0.07635197043418884,-0.03995732590556145,0.04078191891312599,0.060128677636384964,0.07254581898450851,0.06967505812644958,0.08909134566783905,0.015957793220877647,-0.014873594976961613,-0.04674159362912178,-0.013411278836429119,0.06513476371765137,0.05090585723519325,0.05148351192474365,0.0070921811275184155]],
    #     limit=1000
    # )
    # file_obj = VECTOR_DB_CLIENT.get(id)
    # log.debug(file_obj)

    if not file_obj:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=ERROR_MESSAGES.NOT_FOUND,
        )
    
    metadata = file_obj.metadatas[0][0]
    user_id = metadata.get("created_by", "")

    if (
        user_id == user.id
        or user.role == "admin"
        or has_access_to_file(id, "read", user)
    ):
        return {
            "id": id,
            "filename": metadata.get("name", ""),
            "user_id": user_id,
            "embedding_config": literal_eval(metadata.get("embedding_config", r"{}")),
            "chunks": file_obj.documents[0]
        } if file_obj else {}
    else:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=ERROR_MESSAGES.NOT_FOUND,
        )


############################
# UpdateKnowledgeById
############################


@router.post("/{id}/update", response_model=Optional[KnowledgeFilesResponse])
async def update_knowledge_by_id(
    id: str,
    form_data: KnowledgeForm,
    user=Depends(get_verified_user),
):
    knowledge = Knowledges.get_knowledge_by_id(id=id)
    if not knowledge:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=ERROR_MESSAGES.NOT_FOUND,
        )
    # Is the user the original creator, in a group with write access, or an admin
    if (
        knowledge.user_id != user.id
        and not has_access(user.id, "write", knowledge.access_control)
        and user.role != "admin"
    ):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=ERROR_MESSAGES.ACCESS_PROHIBITED,
        )

    knowledge = Knowledges.update_knowledge_by_id(id=id, form_data=form_data)
    if knowledge:
        file_ids = knowledge.data.get("file_ids", []) if knowledge.data else []
        files = Files.get_files_by_ids(file_ids)

        return KnowledgeFilesResponse(
            **knowledge.model_dump(),
            files=files,
        )
    else:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=ERROR_MESSAGES.ID_TAKEN,
        )


############################
# AddFileToKnowledge
############################


class KnowledgeFileIdForm(BaseModel):
    file_id: str


@router.post("/{id}/file/add", response_model=Optional[KnowledgeFilesResponse])
def add_file_to_knowledge_by_id(
    request: Request,
    id: str,
    form_data: KnowledgeFileIdForm,
    user=Depends(get_verified_user),
):
    knowledge = Knowledges.get_knowledge_by_id(id=id)

    if not knowledge:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=ERROR_MESSAGES.NOT_FOUND,
        )

    if (
        knowledge.user_id != user.id
        and not has_access(user.id, "write", knowledge.access_control)
        and user.role != "admin"
    ):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=ERROR_MESSAGES.ACCESS_PROHIBITED,
        )

    file = Files.get_file_by_id(form_data.file_id)
    if not file:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=ERROR_MESSAGES.NOT_FOUND,
        )
    if not file.data:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=ERROR_MESSAGES.FILE_NOT_PROCESSED,
        )

    # Add content to the vector database
    try:
        process_file(
            request,
            ProcessFileForm(file_id=form_data.file_id, collection_name=id),
            user=user,
        )
    except Exception as e:
        log.debug(e)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e),
        )

    if knowledge:
        data = knowledge.data or {}
        file_ids = data.get("file_ids", [])

        if form_data.file_id not in file_ids:
            file_ids.append(form_data.file_id)
            data["file_ids"] = file_ids

            knowledge = Knowledges.update_knowledge_data_by_id(id=id, data=data)

            if knowledge:
                files = Files.get_file_metadatas_by_ids(file_ids)

                return KnowledgeFilesResponse(
                    **knowledge.model_dump(),
                    files=files,
                )
            else:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=ERROR_MESSAGES.DEFAULT("knowledge"),
                )
        else:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=ERROR_MESSAGES.DEFAULT("file_id"),
            )
    else:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=ERROR_MESSAGES.NOT_FOUND,
        )


@router.post("/{id}/file/update", response_model=Optional[KnowledgeFilesResponse])
def update_file_from_knowledge_by_id(
    request: Request,
    id: str,
    form_data: KnowledgeFileIdForm,
    user=Depends(get_verified_user),
):
    knowledge = Knowledges.get_knowledge_by_id(id=id)
    if not knowledge:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=ERROR_MESSAGES.NOT_FOUND,
        )

    if (
        knowledge.user_id != user.id
        and not has_access(user.id, "write", knowledge.access_control)
        and user.role != "admin"
    ):

        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=ERROR_MESSAGES.ACCESS_PROHIBITED,
        )

    file = Files.get_file_by_id(form_data.file_id)
    if not file:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=ERROR_MESSAGES.NOT_FOUND,
        )

    # Remove content from the vector database
    VECTOR_DB_CLIENT.delete(
        collection_name=knowledge.id, filter={"file_id": form_data.file_id}
    )

    # Add content to the vector database
    try:
        process_file(
            request,
            ProcessFileForm(file_id=form_data.file_id, collection_name=id),
            user=user,
        )
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e),
        )

    if knowledge:
        data = knowledge.data or {}
        file_ids = data.get("file_ids", [])

        files = Files.get_file_metadatas_by_ids(file_ids)

        return KnowledgeFilesResponse(
            **knowledge.model_dump(),
            files=files,
        )
    else:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=ERROR_MESSAGES.NOT_FOUND,
        )


############################
# RemoveFileFromKnowledge
############################


@router.post("/{id}/file/remove", response_model=Optional[KnowledgeFilesResponse])
def remove_file_from_knowledge_by_id(
    id: str,
    form_data: KnowledgeFileIdForm,
    user=Depends(get_verified_user),
):
    knowledge = Knowledges.get_knowledge_by_id(id=id)
    if not knowledge:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=ERROR_MESSAGES.NOT_FOUND,
        )

    if (
        knowledge.user_id != user.id
        and not has_access(user.id, "write", knowledge.access_control)
        and user.role != "admin"
    ):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=ERROR_MESSAGES.ACCESS_PROHIBITED,
        )

    file = Files.get_file_by_id(form_data.file_id)
    if not file:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=ERROR_MESSAGES.NOT_FOUND,
        )

    # Remove content from the vector database
    try:
        VECTOR_DB_CLIENT.delete(
            collection_name=knowledge.id, filter={"file_id": form_data.file_id}
        )
    except Exception as e:
        log.debug("This was most likely caused by bypassing embedding processing")
        log.debug(e)
        pass

    try:
        # Remove the file's collection from vector database
        file_collection = f"file-{form_data.file_id}"
        if VECTOR_DB_CLIENT.has_collection(collection_name=file_collection):
            VECTOR_DB_CLIENT.delete_collection(collection_name=file_collection)
    except Exception as e:
        log.debug("This was most likely caused by bypassing embedding processing")
        log.debug(e)
        pass

    # Delete file from database
    Files.delete_file_by_id(form_data.file_id)

    if knowledge:
        data = knowledge.data or {}
        file_ids = data.get("file_ids", [])

        if form_data.file_id in file_ids:
            file_ids.remove(form_data.file_id)
            data["file_ids"] = file_ids

            knowledge = Knowledges.update_knowledge_data_by_id(id=id, data=data)

            if knowledge:
                files = Files.get_file_metadatas_by_ids(file_ids)

                return KnowledgeFilesResponse(
                    **knowledge.model_dump(),
                    files=files,
                )
            else:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=ERROR_MESSAGES.DEFAULT("knowledge"),
                )
        else:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=ERROR_MESSAGES.DEFAULT("file_id"),
            )
    else:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=ERROR_MESSAGES.NOT_FOUND,
        )


############################
# DeleteKnowledgeById
############################


@router.delete("/{id}/delete", response_model=bool)
async def delete_knowledge_by_id(id: str, user=Depends(get_verified_user)):
    knowledge = Knowledges.get_knowledge_by_id(id=id)
    if not knowledge:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=ERROR_MESSAGES.NOT_FOUND,
        )

    if (
        knowledge.user_id != user.id
        and not has_access(user.id, "write", knowledge.access_control)
        and user.role != "admin"
    ):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=ERROR_MESSAGES.ACCESS_PROHIBITED,
        )

    log.info(f"Deleting knowledge base: {id} (name: {knowledge.name})")

    # Get all models
    models = Models.get_all_models()
    log.info(f"Found {len(models)} models to check for knowledge base {id}")

    # Update models that reference this knowledge base
    for model in models:
        if model.meta and hasattr(model.meta, "knowledge"):
            knowledge_list = model.meta.knowledge or []
            # Filter out the deleted knowledge base
            updated_knowledge = [k for k in knowledge_list if k.get("id") != id]

            # If the knowledge list changed, update the model
            if len(updated_knowledge) != len(knowledge_list):
                log.info(f"Updating model {model.id} to remove knowledge base {id}")
                model.meta.knowledge = updated_knowledge
                # Create a ModelForm for the update
                model_form = ModelForm(
                    id=model.id,
                    name=model.name,
                    base_model_id=model.base_model_id,
                    meta=model.meta,
                    params=model.params,
                    access_control=model.access_control,
                    is_active=model.is_active,
                )
                Models.update_model_by_id(model.id, model_form)

    # Clean up vector DB
    try:
        VECTOR_DB_CLIENT.delete_collection(collection_name=id)
    except Exception as e:
        log.debug(e)
        pass
    result = Knowledges.delete_knowledge_by_id(id=id)
    return result


############################
# ResetKnowledgeById
############################


@router.post("/{id}/reset", response_model=Optional[KnowledgeResponse])
async def reset_knowledge_by_id(id: str, user=Depends(get_verified_user)):
    knowledge = Knowledges.get_knowledge_by_id(id=id)
    if not knowledge:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=ERROR_MESSAGES.NOT_FOUND,
        )

    if (
        knowledge.user_id != user.id
        and not has_access(user.id, "write", knowledge.access_control)
        and user.role != "admin"
    ):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=ERROR_MESSAGES.ACCESS_PROHIBITED,
        )

    try:
        VECTOR_DB_CLIENT.delete_collection(collection_name=id)
    except Exception as e:
        log.debug(e)
        pass

    knowledge = Knowledges.update_knowledge_data_by_id(id=id, data={"file_ids": []})

    return knowledge


############################
# AddFilesToKnowledge
############################


@router.post("/{id}/files/batch/add", response_model=Optional[KnowledgeFilesResponse])
def add_files_to_knowledge_batch(
    request: Request,
    id: str,
    form_data: list[KnowledgeFileIdForm],
    user=Depends(get_verified_user),
):
    """
    Add multiple files to a knowledge base
    """
    knowledge = Knowledges.get_knowledge_by_id(id=id)
    if not knowledge:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=ERROR_MESSAGES.NOT_FOUND,
        )

    if (
        knowledge.user_id != user.id
        and not has_access(user.id, "write", knowledge.access_control)
        and user.role != "admin"
    ):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=ERROR_MESSAGES.ACCESS_PROHIBITED,
        )

    # Get files content
    log.info(f"files/batch/add - {len(form_data)} files")
    files: List[FileModel] = []
    for form in form_data:
        file = Files.get_file_by_id(form.file_id)
        if not file:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"File {form.file_id} not found",
            )
        files.append(file)

    # Process files
    try:
        result = process_files_batch(
            request=request,
            form_data=BatchProcessFilesForm(files=files, collection_name=id),
            user=user,
        )
    except Exception as e:
        log.error(
            f"add_files_to_knowledge_batch: Exception occurred: {e}", exc_info=True
        )
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))

    # Add successful files to knowledge base
    data = knowledge.data or {}
    existing_file_ids = data.get("file_ids", [])

    # Only add files that were successfully processed
    successful_file_ids = [r.file_id for r in result.results if r.status == "completed"]
    for file_id in successful_file_ids:
        if file_id not in existing_file_ids:
            existing_file_ids.append(file_id)

    data["file_ids"] = existing_file_ids
    knowledge = Knowledges.update_knowledge_data_by_id(id=id, data=data)

    # If there were any errors, include them in the response
    if result.errors:
        error_details = [f"{err.file_id}: {err.error}" for err in result.errors]
        return KnowledgeFilesResponse(
            **knowledge.model_dump(),
            files=Files.get_file_metadatas_by_ids(existing_file_ids),
            warnings={
                "message": "Some files failed to process",
                "errors": error_details,
            },
        )

    return KnowledgeFilesResponse(
        **knowledge.model_dump(),
        files=Files.get_file_metadatas_by_ids(existing_file_ids),
    )
