"""``Idempotency-Key`` for writes (6.4).

Phones retry when the network drops. A write decorated with ``@idempotent`` stores its
first response under (user, key); a retry with the same key and the same body gets that
response back without running again. The same key with a different body is refused.
"""

import functools
import hashlib
import json

from django.db import IntegrityError, transaction
from rest_framework import status
from rest_framework.response import Response

from .models import IdempotencyRecord
from .problems import Problem

HEADER = "HTTP_IDEMPOTENCY_KEY"


def _request_hash(request) -> str:
    try:
        body = json.dumps(request.data, sort_keys=True, default=str)
    except (TypeError, ValueError):
        body = repr(request.data)
    return hashlib.sha256(f"{request.method} {request.path}\n{body}".encode()).hexdigest()


def idempotent(required: bool = True):
    def decorator(view_method):
        @functools.wraps(view_method)
        def wrapper(self, request, *args, **kwargs):
            key = request.META.get(HEADER, "").strip()
            if not key:
                if required:
                    raise Problem("idempotency_key_required",
                                  "Send an Idempotency-Key header with this request.",
                                  status.HTTP_428_PRECONDITION_REQUIRED)
                return view_method(self, request, *args, **kwargs)
            if len(key) > 100:
                raise Problem("idempotency_key_invalid", "The Idempotency-Key is too long.")

            fingerprint = _request_hash(request)
            existing = IdempotencyRecord.objects.filter(user=request.user, key=key).first()
            if existing:
                if existing.request_hash != fingerprint or existing.scope != request.path:
                    raise Problem("idempotency_key_reused",
                                  "This Idempotency-Key was already used for another request.",
                                  status.HTTP_422_UNPROCESSABLE_ENTITY)
                return Response(existing.body, status=existing.status_code,
                                headers={"Idempotent-Replayed": "true"})

            response = view_method(self, request, *args, **kwargs)
            if 200 <= response.status_code < 300:
                try:
                    with transaction.atomic():
                        IdempotencyRecord.objects.create(
                            user=request.user, key=key, scope=request.path,
                            request_hash=fingerprint, status_code=response.status_code,
                            body=response.data if response.data is not None else {},
                        )
                except IntegrityError:
                    pass  # a parallel retry stored it first; both responses are the same
            return response

        return wrapper

    return decorator
