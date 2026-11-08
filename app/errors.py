"""Erreurs renvoyées au frontend sous la forme {"error": "message en français"}."""

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlalchemy.exc import DataError


class ApiError(Exception):
    def __init__(self, message: str, status: int = 400, **extra):
        self.message, self.status, self.extra = message, status, extra


def not_found(message: str = "Introuvable.") -> ApiError:
    return ApiError(message, 404)


def install(app: FastAPI) -> None:
    @app.exception_handler(ApiError)
    async def _api_error(_: Request, e: ApiError):
        return JSONResponse({"error": e.message, **e.extra}, status_code=e.status)

    @app.exception_handler(DataError)
    async def _data_error(_: Request, e: DataError):
        # Valeur que la base refuse (caractère nul, texte aberrant…) : refusée proprement, jamais une erreur 500
        return JSONResponse({"error": "Certains caractères ne sont pas acceptés. Vérifiez votre saisie."}, status_code=400)

    @app.exception_handler(RequestValidationError)
    async def _validation(_: Request, e: RequestValidationError):
        # Les messages détaillés sont produits par les validations métier ; ici, la requête est mal formée
        return JSONResponse({"error": "Requête invalide."}, status_code=400)
