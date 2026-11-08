from __future__ import annotations

import re

ANSI_ESCAPE_RE = re.compile(r"\x1b\[[0-9;]*m|\[\d+(?:;\d+)*m")


class AppError(Exception):
    def __init__(
        self,
        code: str,
        message: str,
        *,
        detail: str | None = None,
        retryable: bool = False,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.detail = detail
        self.retryable = retryable


class InvalidSoundCloudUrl(AppError):
    def __init__(self, value: str) -> None:
        super().__init__(
            "invalid_url",
            f"不是有效的 SoundCloud 链接：{value!r}",
        )


class RightsNotAcknowledged(AppError):
    def __init__(self) -> None:
        super().__init__(
            "rights_not_acknowledged",
            "请先确认你有权保存该内容。",
        )


class DrmProtectedError(AppError):
    def __init__(self, detail: str) -> None:
        super().__init__(
            "drm_protected",
            "该曲目受 DRM 保护，无法通过下载工具保存。"
            "请选择其他非 DRM 版本或使用版权方官方下载渠道。",
            detail=detail,
        )


class TaskCancelled(AppError):
    def __init__(self, detail: str = "任务已取消") -> None:
        super().__init__("cancelled", detail)


class AuthenticationRequired(AppError):
    def __init__(self, detail: str) -> None:
        super().__init__(
            "authentication_required",
            "该内容需要登录或有效权限，请检查 Cookie 或账号权限。",
            detail=detail,
        )


class RateLimitedError(AppError):
    def __init__(self, detail: str) -> None:
        super().__init__(
            "rate_limited",
            "请求过于频繁，请稍后重试。",
            detail=detail,
            retryable=True,
        )


class NetworkError(AppError):
    def __init__(self, detail: str) -> None:
        super().__init__(
            "network_error",
            "网络连接失败，请检查网络或代理设置。",
            detail=detail,
            retryable=True,
        )


class InvalidMediaError(AppError):
    def __init__(self, detail: str) -> None:
        super().__init__(
            "invalid_media",
            "下载结果不是有效的音频文件。",
            detail=detail,
        )


def strip_ansi(value: object) -> str:
    return ANSI_ESCAPE_RE.sub("", str(value)).strip()


def map_exception(exc: BaseException) -> AppError:
    if isinstance(exc, AppError):
        return exc
    text = strip_ansi(exc) or exc.__class__.__name__
    lowered = text.casefold()
    if "drm protected" in lowered:
        return DrmProtectedError(text)
    if "http error 429" in lowered or "too many requests" in lowered:
        return RateLimitedError(text)
    if "http error 401" in lowered or "http error 403" in lowered:
        return AuthenticationRequired(text)
    if any(
        marker in lowered
        for marker in (
            "nodename nor servname",
            "temporary failure in name resolution",
            "network is unreachable",
            "connection reset",
            "timed out",
            "timeout",
        )
    ):
        return NetworkError(text)
    if "no space left" in lowered:
        return AppError(
            "disk_full",
            "磁盘空间不足，无法完成下载。",
            detail=text,
        )
    return AppError("operation_failed", "操作失败，请查看诊断信息。", detail=text)


def user_error_text(exc: BaseException, *, include_detail: bool = True) -> str:
    error = map_exception(exc)
    if include_detail and error.detail and error.detail != error.message:
        return f"{error.message}\n\n{strip_ansi(error.detail)}"
    return error.message
