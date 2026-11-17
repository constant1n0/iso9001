"""Release draft screens; strict form boundary and web-only verification edits."""
from typing import Any
from flask import Blueprint, abort, flash, redirect, render_template, request, url_for
from flask.typing import ResponseReturnValue
from flask_login import login_required
from sqlalchemy import select
from ..audit_notifications import local_today
from ..extensions import db
from ..forms import (BaseForm, ProductReleaseForm, ReleaseVerificationForm,
                     ReleaseAuthorizationForm, ReleaseRejectionForm, person_choices)
from ..models import Cliente, ProductionState, ReleaseStatus, RoleEnum
from ..services import people, production, product_release as service
from ..utils.permissions import require_permission
from ..utils.web_actor import current_actor
from .auditoria_routes import committed
from .people_routes import form_data, saved
from .production_routes import _refused

bp = Blueprint('release', __name__, url_prefix='/liberacion')


def _refuse(form: Any, *, optional: frozenset[str] = frozenset()) -> bool:
    refused = _refused(form)
    if request.method == 'POST' and (missing := {f.name for f in form if f.name != 'csrf_token'} - optional - set(request.form)):
        flash(f'Faltan campos del formulario: {", ".join(sorted(missing))}.', 'danger')
        refused = True
    return refused


@bp.route('/')
@login_required
@require_permission('read', 'release')
def list_releases() -> ResponseReturnValue:
    """Ignore unrecognized filters, including nonexistent citation ids."""
    who = current_actor()
    choices = {'status': [(m.name, m.value) for m in ReleaseStatus],
        'concession': [('true', 'Con concesión'), ('false', 'Sin concesión')],
        'order_id': [(str(o.id), o.code) for o in production.orders.list_(db.session, who)],
        'customer_id': [(str(c.id), c.nombre) for c in db.session.scalars(select(Cliente).order_by(Cliente.id))]}
    selected = {k: v for k in choices if (v := request.args.get(k, '')) in dict(choices[k])}
    filters = {k: (v == 'true') if k == 'concession' else
        int(v) if k.endswith('_id') else v for k, v in selected.items()}
    return render_template('release/list.html', choices=choices, selected=selected,
        items=service.releases.list_(db.session, who, **filters))


@bp.route('/<int:release_id>')
@login_required
@require_permission('read', 'release')
def show_release(release_id: int) -> ResponseReturnValue:
    """Show immutable decisions and administrator-only draft decision forms."""
    return _detail(release_id)


def _detail(release_id: int, authorization: Any = None,
            rejection: Any = None) -> str:
    who = current_actor()
    authorization = authorization or ReleaseAuthorizationForm(formdata=None)
    authorization.concession_approver_id.choices = person_choices(people.choices(db.session, who))
    return render_template('release/detail.html',
        item=service.releases.get(db.session, who, release_id),
        checks=service.verifications.list_(db.session, who, release_id=release_id),
        action_form=BaseForm(), authorization=authorization,
        rejection=rejection or ReleaseRejectionForm(formdata=None),
        administrator=who.role is RoleEnum.ADMINISTRADOR)


@bp.route('/<int:release_id>/autorizar', methods=['POST'])
@login_required
@require_permission('update', 'release')
def authorize_release(release_id: int) -> ResponseReturnValue:
    """Authorize with server clock and linked independent person, never posted ids."""
    return _decide(release_id, rejecting=False)


@bp.route('/<int:release_id>/rechazar', methods=['POST'])
@login_required
@require_permission('update', 'release')
def reject_release(release_id: int) -> ResponseReturnValue:
    """Retain the rejection reason and immutable authority."""
    return _decide(release_id, rejecting=True)


def _decide(release_id: int, *, rejecting: bool) -> ResponseReturnValue:
    who = current_actor()
    if who.role is not RoleEnum.ADMINISTRADOR:
        flash('Solo un administrador puede decidir la liberación.', 'danger')
        return redirect(url_for('release.show_release', release_id=release_id))
    form = ReleaseRejectionForm() if rejecting else ReleaseAuthorizationForm()
    if not rejecting:
        form.concession_approver_id.choices = person_choices(people.choices(db.session, who))
    # An unchecked native checkbox is not a successful control. Every text/date/
    # select control must still be present, including blank optional values.
    refused = _refuse(form, optional=frozenset() if rejecting else frozenset({'concession'}))
    if not refused and form.validate_on_submit():
        operation = (lambda: service.reject(db.session, who, release_id,
            reason=form.reason.data, today=local_today())) if rejecting else (
            lambda: service.authorize(db.session, who, release_id, today=local_today(),
                **form_data(form, {'concession', 'concession_reason', 'concession_approver_id',
                                  'customer_approval_date', 'customer_approval_means'})))
        if saved(operation):
            return redirect(url_for('release.show_release', release_id=release_id))
    return _detail(release_id, rejection=form if rejecting else None,
                   authorization=None if rejecting else form), 400 if refused else 200


@bp.route('/nueva', methods=['GET', 'POST'])
@login_required
@require_permission('create', 'release')
def new_release() -> ResponseReturnValue:
    """Start a draft for one finished order."""
    return _edit(None)


@bp.route('/<int:release_id>/editar', methods=['GET', 'POST'])
@login_required
@require_permission('update', 'release')
def edit_release(release_id: int) -> ResponseReturnValue:
    """Edit draft notes, retaining the original order."""
    return _edit(release_id)


def _submit(form: Any, operation: Any, title: str, refused: bool) -> ResponseReturnValue:
    if not refused and form.validate_on_submit() and (result := saved(operation)):
        return redirect(url_for('release.show_release', release_id=getattr(result, 'release_id', result.id)))
    return render_template('release/form.html', form=form, title=title), 400 if refused else 200


def _edit(release_id: int | None) -> ResponseReturnValue:
    who = current_actor()
    row = None if release_id is None else service.releases.get(db.session, who, release_id)
    form = ProductReleaseForm(obj=row)
    form.order_id.choices = [('', '— Elige una orden —'), *((o.id, o.code)
        for o in production.orders.list_(db.session, who)
        if (row is None and o.state is ProductionState.finished) or (row is not None and o.id == row.order_id))]
    refused = _refuse(form)
    if row is not None and row.status is not ReleaseStatus.draft and not refused:
        flash(service.READ_ONLY, 'danger')
        return redirect(url_for('release.show_release', release_id=release_id))
    data = lambda: form_data(form, service.SPEC.writable)
    return _submit(form, lambda: service.releases.create(db.session, who, data()) if row is None
        else service.releases.update(db.session, who, row.id, data()), 'Nueva liberación' if row is None else 'Editar borrador', refused)


@bp.route('/<int:release_id>/verificaciones/nueva', methods=['GET', 'POST'])
@login_required
@require_permission('create', 'release')
def new_verification(release_id: int) -> ResponseReturnValue:
    """Add dated evidence while the release remains a draft."""
    return _verification(release_id, None)


@bp.route('/<int:release_id>/verificaciones/<int:check_id>/editar', methods=['GET', 'POST'])
@login_required
@require_permission('update', 'release')
def edit_verification(release_id: int, check_id: int) -> ResponseReturnValue:
    """Edit evidence without moving it to another release."""
    return _verification(release_id, check_id)


def _check_child(release_id: int, check_id: int) -> Any:
    child = service.verifications.get(db.session, current_actor(), check_id)
    if child.release_id != release_id:
        abort(404)
    return child


def _verification(release_id: int, check_id: int | None) -> ResponseReturnValue:
    who = current_actor()
    parent = service.releases.get(db.session, who, release_id)
    row = None if check_id is None else _check_child(release_id, check_id)
    form = ReleaseVerificationForm(obj=row)
    form.person_id.choices = person_choices(people.choices(db.session, who, include=getattr(row, 'person_id', None)))
    if request.method == 'GET':
        form.result.data = row.result.name if row else 'conforming'
        form.date.data = row.date if row else local_today()
    refused = _refuse(form)
    if parent.status is not ReleaseStatus.draft and not refused:
        flash(service.READ_ONLY, 'danger')
        return redirect(url_for('release.show_release', release_id=release_id))
    data = lambda: form_data(form, service.VERIFICATION_SPEC.writable - {'release_id'})
    return _submit(form, lambda: service.verifications.create(db.session, who,
        data() | {'release_id': release_id}, today=local_today()) if row is None else
        service.verifications.update(db.session, who, row.id, data(), today=local_today()), 'Verificación de liberación', refused)


@bp.route('/<int:release_id>/eliminar', methods=['POST'])
@login_required
@require_permission('delete', 'release')
def delete_release(release_id: int) -> ResponseReturnValue:
    """Administrators delete drafts together with their audited verifications."""
    form = BaseForm()
    refused = _refuse(form)
    if not refused and form.validate_on_submit() and committed(lambda: service.releases.delete(db.session, current_actor(), release_id)):
        return redirect(url_for('release.list_releases'))
    return redirect(url_for('release.show_release', release_id=release_id)), 400 if refused else 302


@bp.route('/<int:release_id>/verificaciones/<int:check_id>/eliminar', methods=['POST'])
@login_required
@require_permission('update', 'release')
def delete_verification(release_id: int, check_id: int) -> ResponseReturnValue:
    """Draft writers remove evidence; foreign children are never accepted."""
    form = BaseForm()
    refused = _refuse(form)
    if not refused and form.validate_on_submit():
        _check_child(release_id, check_id)
        committed(lambda: service.verifications.delete(db.session, current_actor(), check_id))
    return redirect(url_for('release.show_release', release_id=release_id)), 400 if refused else 302
