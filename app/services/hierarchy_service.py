"""Serviço de Gestão da Hierarquia do VMSun.

Estrutura: Cliente -> Local -> NVR -> Canal (Câmera) e Câmeras IP Diretas.
Também gerencia Grupos Operacionais (Perímetro, Acessos, Câmeras Críticas).
"""

from typing import Any, Dict, List, Optional
from sqlalchemy.orm import Session

from app.db.models import Camera, CameraGroup, Customer, Nvr, Site


def get_aggregated_status(online_count: int, total_count: int) -> str:
    if total_count == 0:
        return "empty"
    if online_count == total_count:
        return "online"
    if online_count > 0:
        return "degraded"
    return "offline"


class HierarchyService:
    def get_full_tree(self, db: Session) -> Dict[str, Any]:
        customers = db.query(Customer).filter(Customer.is_active == True).order_by(Customer.name.asc()).all()  # noqa: E712
        sites_without_customer = (
            db.query(Site).filter(Site.customer_id == None, Site.is_active == True).order_by(Site.name.asc()).all()  # noqa: E711, E712
        )
        unassigned_cameras = (
            db.query(Camera)
            .filter(
                Camera.is_deleted == False,  # noqa: E712
                Camera.customer_id == None,  # noqa: E711
                Camera.site_id == None,      # noqa: E711
                Camera.nvr_id == None,       # noqa: E711
            )
            .order_by(Camera.name.asc())
            .all()
        )

        customer_list = []
        for cust in customers:
            cust_tree = self._build_customer_node(db, cust)
            customer_list.append(cust_tree)

        standalone_sites = [self._build_site_node(db, s) for s in sites_without_customer]

        # Grupos operacionais
        groups = db.query(CameraGroup).order_by(CameraGroup.name.asc()).all()
        group_list = []
        for g in groups:
            cams = [c.id for c in g.cameras if not c.is_deleted]
            group_list.append({
                "id": g.id,
                "name": g.name,
                "description": g.description,
                "camera_ids": cams,
            })

        direct_cameras_data = [self._format_camera(c) for c in unassigned_cameras]

        return {
            "customers": customer_list,
            "standalone_sites": standalone_sites,
            "direct_cameras": direct_cameras_data,
            "groups": group_list,
        }

    def _build_customer_node(self, db: Session, customer: Customer) -> Dict[str, Any]:
        sites = db.query(Site).filter(Site.customer_id == customer.id, Site.is_active == True).order_by(Site.name.asc()).all()  # noqa: E712
        site_nodes = [self._build_site_node(db, s) for s in sites]

        total_cameras = sum(s["total_cameras"] for s in site_nodes)
        online_cameras = sum(s["online_cameras"] for s in site_nodes)

        return {
            "id": customer.id,
            "name": customer.name,
            "code": customer.code,
            "status": get_aggregated_status(online_cameras, total_cameras),
            "total_cameras": total_cameras,
            "online_cameras": online_cameras,
            "sites": site_nodes,
        }

    def _build_site_node(self, db: Session, site: Site) -> Dict[str, Any]:
        nvrs = db.query(Nvr).filter(Nvr.site_id == site.id).order_by(Nvr.name.asc()).all()
        direct_cams = (
            db.query(Camera)
            .filter(
                Camera.site_id == site.id,
                Camera.nvr_id == None,  # noqa: E711
                Camera.is_deleted == False,  # noqa: E712
            )
            .order_by(Camera.name.asc())
            .all()
        )

        nvr_nodes = [self._build_nvr_node(db, nvr) for nvr in nvrs]
        direct_nodes = [self._format_camera(c) for c in direct_cams]

        nvr_cameras = sum(n["total_cameras"] for n in nvr_nodes)
        nvr_online = sum(n["online_cameras"] for n in nvr_nodes)

        direct_online = sum(1 for c in direct_nodes if c["status"] == "running" or c["status"] == "online")
        total_cams = nvr_cameras + len(direct_nodes)
        total_online = nvr_online + direct_online

        return {
            "id": site.id,
            "name": site.name,
            "code": site.code,
            "address": site.address,
            "status": get_aggregated_status(total_online, total_cams),
            "total_cameras": total_cams,
            "online_cameras": total_online,
            "nvrs": nvr_nodes,
            "direct_cameras": direct_nodes,
        }

    def _build_nvr_node(self, db: Session, nvr: Nvr) -> Dict[str, Any]:
        channels = (
            db.query(Camera)
            .filter(Camera.nvr_id == nvr.id, Camera.is_deleted == False)  # noqa: E712
            .order_by(Camera.channel_number.asc().nulls_last(), Camera.name.asc())
            .all()
        )
        channel_nodes = [self._format_camera(c) for c in channels]

        online_count = sum(1 for c in channel_nodes if c["status"] in {"running", "online"})
        total_count = len(channel_nodes)

        return {
            "id": nvr.id,
            "name": nvr.name,
            "host": nvr.host,
            "port": nvr.port,
            "vendor": nvr.vendor,
            "total_channels": nvr.total_channels,
            "status": get_aggregated_status(online_count, total_count),
            "online_cameras": online_count,
            "total_cameras": total_count,
            "channels": channel_nodes,
        }

    def _format_camera(self, camera: Camera) -> Dict[str, Any]:
        is_online = str(camera.status).lower() in {"running", "online", "running_motion_test"}
        return {
            "id": camera.id,
            "name": camera.name,
            "channel_number": camera.channel_number,
            "status": "online" if is_online else "offline",
            "raw_status": camera.status,
            "ip": camera.ip,
            "has_main": bool(camera.rtsp_url),
            "has_sub": bool(camera.sub_rtsp_url),
        }

    # CRUD auxiliar
    def create_customer(self, db: Session, name: str, code: Optional[str] = None, description: Optional[str] = None) -> Customer:
        customer = Customer(name=name, code=code, description=description)
        db.add(customer)
        db.commit()
        db.refresh(customer)
        return customer

    def create_site(self, db: Session, name: str, customer_id: Optional[int] = None, code: Optional[str] = None, address: Optional[str] = None) -> Site:
        site = Site(name=name, customer_id=customer_id, code=code, address=address)
        db.add(site)
        db.commit()
        db.refresh(site)
        return site

    def create_nvr(self, db: Session, name: str, host: str, username: str, password: str, site_id: Optional[int] = None, vendor: str = "generic", port: int = 80, total_channels: int = 16) -> Nvr:
        nvr = Nvr(
            name=name,
            host=host,
            site_id=site_id,
            vendor=vendor,
            port=port,
            username=username,
            password=password,
            total_channels=total_channels,
        )
        db.add(nvr)
        db.commit()
        db.refresh(nvr)
        return nvr

    def create_group(self, db: Session, name: str, description: Optional[str] = None, camera_ids: Optional[List[int]] = None) -> CameraGroup:
        group = CameraGroup(name=name, description=description)
        if camera_ids:
            cams = db.query(Camera).filter(Camera.id.in_(camera_ids)).all()
            group.cameras = cams
        db.add(group)
        db.commit()
        db.refresh(group)
        return group


hierarchy_service = HierarchyService()
