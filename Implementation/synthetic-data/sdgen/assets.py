"""One entry point to render any held attachment asset from its locked document_spec, by media type.
EML attachments need authored prose and are therefore not handled here (authored overlay path)."""
from .render_generic import render_spec_csv, render_spec_png, render_spec_xlsx
from .render_image import render_upload_receipt
from .render_pdf import render_document

XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


class AuthoringRequired(Exception):
    """This asset carries prose (an email); it must come from an authored overlay."""


def render_asset(asset, owner_name):
    mt, spec = asset["media_type"], asset["document_spec"]
    if mt == "application/pdf":
        return render_document(asset, owner_name), None
    if mt == "text/csv":
        return render_spec_csv(asset), None
    if mt == XLSX:
        return render_spec_xlsx(asset, owner_name), None
    if mt == "image/png":
        return render_spec_png(asset)
    if mt == "image/jpeg":
        f = spec["fields"]
        if "upload_reference" in f:
            return render_upload_receipt(asset, spec["rows"], {k: f[k] for k in ("upload_reference", "uploaded_at", "member_account_number")})
        raise NotImplementedError(f"{asset['filename']}: JPEG layout other than the upload receipt")
    if mt == "message/rfc822":
        raise AuthoringRequired(asset["filename"])
    raise NotImplementedError(f"{asset['filename']}: media type {mt}")
