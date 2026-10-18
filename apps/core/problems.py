"""Errors as RFC 9457 problem details.

Every API error has the same shape, so the app shows one kind of message. A refusal by a
chama rule (from Phase 1) adds ``rule_refs`` and the reason in the user's language.
"""

from rest_framework import exceptions, status
from rest_framework.response import Response
from rest_framework.views import exception_handler

PROBLEM_CONTENT_TYPE = "application/problem+json"


class Problem(exceptions.APIException):
    """Raise for a known refusal: ``raise Problem("otp_expired", "The code has expired.")``."""

    status_code = status.HTTP_400_BAD_REQUEST

    def __init__(self, code: str, detail: str, status_code: int | None = None, **extra):
        super().__init__(detail=detail, code=code)
        if status_code:
            self.status_code = status_code
        self.problem_code = code
        self.extra = extra


def problem_exception_handler(exc, context):
    response = exception_handler(exc, context)
    if response is None:
        return None

    if isinstance(exc, Problem):
        body = {
            "type": f"https://jiranichama.co.ke/problems/{exc.problem_code}",
            "title": exc.problem_code.replace("_", " ").capitalize(),
            "status": response.status_code,
            "detail": str(exc.detail),
            "code": exc.problem_code,
            **exc.extra,
        }
    elif isinstance(exc, exceptions.ValidationError):
        body = {
            "type": "https://jiranichama.co.ke/problems/invalid",
            "title": "Invalid request",
            "status": response.status_code,
            "detail": "Some fields are not valid.",
            "code": "invalid",
            "errors": response.data,
        }
    else:
        detail = response.data.get("detail", "") if isinstance(response.data, dict) else ""
        code = getattr(exc, "default_code", "error")
        body = {
            "type": f"https://jiranichama.co.ke/problems/{code}",
            "title": str(code).replace("_", " ").capitalize(),
            "status": response.status_code,
            "detail": str(detail),
            "code": code,
        }

    return Response(
        body,
        status=response.status_code,
        headers={k: v for k, v in response.items() if k.lower() != "content-type"},
        content_type=PROBLEM_CONTENT_TYPE,
    )
