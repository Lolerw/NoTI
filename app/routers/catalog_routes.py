from typing import List
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from app.database import get_db
from app.models import ShiftBlockCatalog
from app.schemas import ShiftBlockCreate, ShiftBlockUpdate, ShiftBlockResponse

router = APIRouter(prefix="/api/catalog", tags=["Catálogo de Bloques de Turno"])

@router.get("", response_model=List[ShiftBlockResponse])
def get_all_shift_blocks(db: Session = Depends(get_db)):
    """Obtiene todos los bloques de turno configurados (HU-02)."""
    return db.query(ShiftBlockCatalog).all()

@router.post("", response_model=ShiftBlockResponse, status_code=status.HTTP_201_CREATED)
def create_shift_block(block_in: ShiftBlockCreate, db: Session = Depends(get_db)):
    """Crea un nuevo bloque de turno en el catálogo maestro."""
    existing = db.query(ShiftBlockCatalog).filter(ShiftBlockCatalog.name == block_in.name.strip().upper()).first()
    if existing:
        raise HTTPException(status_code=400, detail="Ya existe un bloque de turno con ese nombre")

    block = ShiftBlockCatalog(
        name=block_in.name.strip().upper(),
        color=block_in.color,
        duration_type=block_in.duration_type,
        requires_coverage=block_in.requires_coverage
    )
    db.add(block)
    db.commit()
    db.refresh(block)
    return block

@router.put("/{block_id}", response_model=ShiftBlockResponse)
def update_shift_block(block_id: int, block_in: ShiftBlockUpdate, db: Session = Depends(get_db)):
    """Actualiza un bloque existente en el catálogo."""
    block = db.query(ShiftBlockCatalog).filter(ShiftBlockCatalog.id == block_id).first()
    if not block:
        raise HTTPException(status_code=404, detail="Bloque de turno no encontrado")

    if block_in.name is not None:
        block.name = block_in.name.strip().upper()
    if block_in.color is not None:
        block.color = block_in.color
    if block_in.duration_type is not None:
        block.duration_type = block_in.duration_type
    if block_in.requires_coverage is not None:
        block.requires_coverage = block_in.requires_coverage

    db.commit()
    db.refresh(block)
    return block

@router.delete("/{block_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_shift_block(block_id: int, db: Session = Depends(get_db)):
    """Elimina un bloque del catálogo si no tiene turnos asignados."""
    block = db.query(ShiftBlockCatalog).filter(ShiftBlockCatalog.id == block_id).first()
    if not block:
        raise HTTPException(status_code=404, detail="Bloque de turno no encontrado")

    try:
        db.delete(block)
        db.commit()
    except Exception:
        db.rollback()
        raise HTTPException(status_code=400, detail="No se puede eliminar el bloque porque tiene turnos asignados en el historial")
    return None
