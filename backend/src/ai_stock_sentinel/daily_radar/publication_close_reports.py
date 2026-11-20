"""Capture and replay the exchange reports for a historical publication correction."""

from datetime import date
from urllib.parse import parse_qsl, urlsplit

from ai_stock_sentinel.calibration.research_prices import RecoveringResearchPriceProvider
from ai_stock_sentinel.daily_radar.history_repair import fingerprint
from ai_stock_sentinel.daily_radar.reviewed_publication_close import collect
from ai_stock_sentinel.daily_radar.terminal_integrity import _source


class Reports(RecoveringResearchPriceProvider):
    def __init__(self, provider=None, reports=None):
        super().__init__()
        self.original = provider
        self.reports = {} if reports is None else reports
        self.used = set()

    def _capture_report(self, origin, path, params):
        request = {"url": origin + path, "params": params}
        key = fingerprint(request)
        self.used.add(key)
        if key not in self.reports:
            if self.original is None:
                raise ValueError("publication_report_absent")
            getter = (
                self.original.capture_twse_report
                if origin == "https://www.twse.com.tw"
                else self.original.capture_tpex_report
            )
            payload, proof = getter(path, params)
            self.reports[key] = {"request": request, "payload": payload, "proof": proof}
        entry = self.reports[key]
        payload, proof = entry["payload"], entry["proof"]
        if (
            entry["request"] != request
            or not _source(proof)
            or fingerprint(payload) != proof["snapshot_sha256"]
        ):
            raise ValueError("publication_report_digest_or_identity")
        url = urlsplit(proof["source_url"])
        query = dict(parse_qsl(url.query, keep_blank_values=True))
        if (
            url.scheme + "://" + url.netloc + url.path != request["url"]
            or url.fragment
            or len(parse_qsl(url.query, keep_blank_values=True)) != len(query)
        ):
            raise ValueError("publication_report_url")
        if query != params:
            # The project's range parser also checks raw coverage of the requested
            # interval. A cached range may only expand the two request boundaries.
            if (
                proof.get("requested_subrange") != params
                or set(query) != set(params)
                or "startDate" not in params
                or "endDate" not in params
                or query["startDate"] > params["startDate"]
                or query["endDate"] < params["endDate"]
                or any(query[k] != v for k, v in params.items() if k not in ("startDate", "endDate"))
            ):
                raise ValueError("publication_report_query")
        return payload, proof


def capture(symbol, start, end, provider, *, suspension_days=()):
    sources = Reports(provider)
    collect(symbol, start, end, sources)
    capture = {"symbol": symbol, "start": str(start), "end": str(end), "reports": sources.reports}
    if suspension_days:
        from ai_stock_sentinel.daily_radar.trading_status import collect_target_suspension

        capture["suspensions"] = {
            str(day): collect_target_suspension(provider, symbol, day) for day in sorted(set(suspension_days))
        }
    return capture


def replay(capture):
    reader = Reports(reports=capture["reports"])
    result = collect(
        capture["symbol"], date.fromisoformat(capture["start"]), date.fromisoformat(capture["end"]), reader
    )
    if set(reader.reports) - reader.used:
        raise ValueError("publication_report_unused_source")
    if "suspensions" in capture:
        from ai_stock_sentinel.daily_radar.trading_status import matches

        for day, proof in capture["suspensions"].items():
            target = date.fromisoformat(day)
            if (
                day in result["stock"]
                or reader.has_partial_official_price(capture["symbol"], target)
                or not matches(proof, symbol=capture["symbol"], target=target)
            ):
                raise ValueError("publication_close_unverified_suspension")
        result["suspensions"] = capture["suspensions"]
    return result
