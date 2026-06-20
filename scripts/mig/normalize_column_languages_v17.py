# -*- coding: utf-8 -*-
"""
scripts/mig/normalize_column_languages_v17.py

Normaliza idiomas de columnas clasificatorias en fund_master.
Ejecutar UNA SOLA VEZ tras backup de BD.

Basado en análisis exhaustivo de Fund_master_20260401.xlsx (3.204 fondos).

Columnas normalizadas:
- Type → ESPAÑOL
- Family → ESPAÑOL  
- Theme → INGLÉS
- Subtype → INGLÉS

Strategy NO requiere normalización (ya homogéneo en ES).
"""

import sqlite3
from pathlib import Path
import sys
from datetime import datetime
import shutil

# ============================================================================
# MAPEOS DE TRADUCCIÓN (basados en valores reales encontrados en BD)
# ============================================================================

TYPE_TRANSLATION_MAP = {
    # EN → ES
    'Allocation': 'Asignación',
    'Absolute Return': 'Retorno Absoluto',
    'Commodities': 'Materias Primas',
    'Target Volatility': 'Volatilidad Objetivo',
    'Total Return': 'Retorno Total',
    'Tactical Allocation': 'Asignación Táctica',
    'Real Assets': 'Activos Reales',
    
    # UNKNOWN → ES
    'Target Maturity': 'Vencimiento Objetivo',
    'Floating Rate CP': 'CP Tipo Flotante',
}

FAMILY_TRANSLATION_MAP = {
    # EN → ES
    'RV Core': 'RV Núcleo',
    'Income Oriented': 'Orientado a Ingresos',
    'RF High Yield': 'RF Alto Rendimiento',
}

THEME_TRANSLATION_MAP = {
    # ES → EN
    'Inflación': 'Inflation',
}

SUBTYPE_TRANSLATION_MAP = {
    # ES → EN
    'Fondo Indexado': 'Index Fund',
}


def normalize_languages(db_path, dry_run=True):
    """
    Normaliza idiomas de columnas Type, Family, Theme, Subtype.
    
    Args:
        db_path: Ruta a fondos.sqlite
        dry_run: Si True, solo reporta cambios sin aplicarlos
    """
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    
    print(f"\n{'='*80}")
    print(f"{'[DRY RUN] ' if dry_run else ''}NORMALIZACIÓN DE IDIOMAS - v17")
    print(f"{'='*80}")
    print(f"Base de datos: {db_path}")
    print(f"Fecha: {datetime.now():%Y-%m-%d %H:%M:%S}\n")
    
    total_affected = 0
    
    # ========================================================================
    # 1. Normalizar Type → ESPAÑOL
    # ========================================================================
    print(f"\n{'-'*80}")
    print("1. Normalizando Type → ESPAÑOL")
    print(f"{'-'*80}\n")
    
    type_affected = 0
    for en_value, es_value in TYPE_TRANSLATION_MAP.items():
        affected = conn.execute("""
            SELECT COUNT(*) as n FROM fund_master WHERE Type = ?
        """, (en_value,)).fetchone()['n']
        
        if affected > 0:
            print(f"  '{en_value}' → '{es_value}' ({affected} fondos)")
            type_affected += affected
            
            if not dry_run:
                conn.execute("""
                    UPDATE fund_master SET Type = ? WHERE Type = ?
                """, (es_value, en_value))
    
    print(f"\nTotal Type: {type_affected} fondos afectados")
    total_affected += type_affected
    
    # ========================================================================
    # 2. Normalizar Family → ESPAÑOL
    # ========================================================================
    print(f"\n{'-'*80}")
    print("2. Normalizando Family → ESPAÑOL")
    print(f"{'-'*80}\n")
    
    family_affected = 0
    for en_value, es_value in FAMILY_TRANSLATION_MAP.items():
        affected = conn.execute("""
            SELECT COUNT(*) as n FROM fund_master WHERE Family = ?
        """, (en_value,)).fetchone()['n']
        
        if affected > 0:
            print(f"  '{en_value}' → '{es_value}' ({affected} fondos)")
            family_affected += affected
            
            if not dry_run:
                conn.execute("""
                    UPDATE fund_master SET Family = ? WHERE Family = ?
                """, (es_value, en_value))
    
    print(f"\nTotal Family: {family_affected} fondos afectados")
    total_affected += family_affected
    
    # ========================================================================
    # 3. Normalizar Theme → INGLÉS
    # ========================================================================
    print(f"\n{'-'*80}")
    print("3. Normalizando Theme → INGLÉS")
    print(f"{'-'*80}\n")
    
    theme_affected = 0
    for es_value, en_value in THEME_TRANSLATION_MAP.items():
        affected = conn.execute("""
            SELECT COUNT(*) as n FROM fund_master WHERE Theme = ?
        """, (es_value,)).fetchone()['n']
        
        if affected > 0:
            print(f"  '{es_value}' → '{en_value}' ({affected} fondos)")
            theme_affected += affected
            
            if not dry_run:
                conn.execute("""
                    UPDATE fund_master SET Theme = ? WHERE Theme = ?
                """, (en_value, es_value))
    
    print(f"\nTotal Theme: {theme_affected} fondos afectados")
    total_affected += theme_affected
    
    # ========================================================================
    # 4. Normalizar Subtype → INGLÉS
    # ========================================================================
    print(f"\n{'-'*80}")
    print("4. Normalizando Subtype → INGLÉS")
    print(f"{'-'*80}\n")
    
    subtype_affected = 0
    for es_value, en_value in SUBTYPE_TRANSLATION_MAP.items():
        affected = conn.execute("""
            SELECT COUNT(*) as n FROM fund_master WHERE Subtype = ?
        """, (es_value,)).fetchone()['n']
        
        if affected > 0:
            print(f"  '{es_value}' → '{en_value}' ({affected} fondos)")
            subtype_affected += affected
            
            if not dry_run:
                conn.execute("""
                    UPDATE fund_master SET Subtype = ? WHERE Subtype = ?
                """, (en_value, es_value))
    
    print(f"\nTotal Subtype: {subtype_affected} fondos afectados")
    total_affected += subtype_affected
    
    # ========================================================================
    # RESUMEN Y COMMIT
    # ========================================================================
    print(f"\n{'='*80}")
    print(f"RESUMEN DE NORMALIZACIÓN")
    print(f"{'='*80}")
    print(f"  Type (→ ES):     {type_affected:5} fondos")
    print(f"  Family (→ ES):   {family_affected:5} fondos")
    print(f"  Theme (→ EN):    {theme_affected:5} fondos")
    print(f"  Subtype (→ EN):  {subtype_affected:5} fondos")
    print(f"  {'-'*30}")
    print(f"  TOTAL:           {total_affected:5} fondos afectados")
    print(f"{'='*80}\n")
    
    if not dry_run:
        conn.commit()
        print("✓ Normalización completada y persistida.\n")
    else:
        print("[DRY RUN] No se aplicaron cambios.")
        print("Ejecuta con --apply para persistir.\n")
    
    conn.close()
    return total_affected


def verify_homogeneity(db_path):
    """
    Verifica que columnas estén completamente homogéneas.
    Reporta cualquier valor que parezca estar en idioma incorrecto.
    """
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    
    print(f"\n{'='*80}")
    print("VERIFICACIÓN DE HOMOGENEIDAD POST-MIGRACIÓN")
    print(f"{'='*80}\n")
    
    # Valores esperados en idioma incorrecto (deben ser 0 tras migración)
    issues_found = False
    
    # Verificar Type (debe ser ES)
    type_en_values = list(TYPE_TRANSLATION_MAP.keys())
    if type_en_values:
        type_issues = conn.execute(f"""
            SELECT Type, COUNT(*) as n
            FROM fund_master
            WHERE Type IN ({','.join(['?']*len(type_en_values))})
            GROUP BY Type
        """, type_en_values).fetchall()
        
        if type_issues:
            issues_found = True
            print("⚠️  Type aún tiene valores en INGLÉS:")
            for row in type_issues:
                print(f"     {row['Type']}: {row['n']} fondos")
    
    # Verificar Family (debe ser ES)
    family_en_values = list(FAMILY_TRANSLATION_MAP.keys())
    if family_en_values:
        family_issues = conn.execute(f"""
            SELECT Family, COUNT(*) as n
            FROM fund_master
            WHERE Family IN ({','.join(['?']*len(family_en_values))})
            GROUP BY Family
        """, family_en_values).fetchall()
        
        if family_issues:
            issues_found = True
            print("\n⚠️  Family aún tiene valores en INGLÉS:")
            for row in family_issues:
                print(f"     {row['Family']}: {row['n']} fondos")
    
    # Verificar Theme (debe ser EN)
    theme_es_values = list(THEME_TRANSLATION_MAP.keys())
    if theme_es_values:
        theme_issues = conn.execute(f"""
            SELECT Theme, COUNT(*) as n
            FROM fund_master
            WHERE Theme IN ({','.join(['?']*len(theme_es_values))})
            GROUP BY Theme
        """, theme_es_values).fetchall()
        
        if theme_issues:
            issues_found = True
            print("\n⚠️  Theme aún tiene valores en ESPAÑOL:")
            for row in theme_issues:
                print(f"     {row['Theme']}: {row['n']} fondos")
    
    # Verificar Subtype (debe ser EN)
    subtype_es_values = list(SUBTYPE_TRANSLATION_MAP.keys())
    if subtype_es_values:
        subtype_issues = conn.execute(f"""
            SELECT Subtype, COUNT(*) as n
            FROM fund_master
            WHERE Subtype IN ({','.join(['?']*len(subtype_es_values))})
            GROUP BY Subtype
        """, subtype_es_values).fetchall()
        
        if subtype_issues:
            issues_found = True
            print("\n⚠️  Subtype aún tiene valores en ESPAÑOL:")
            for row in subtype_issues:
                print(f"     {row['Subtype']}: {row['n']} fondos")
    
    if not issues_found:
        print("✅ Todas las columnas están HOMOGÉNEAS (sin mezcla de idiomas)\n")
    else:
        print("\n⚠️  Se encontraron inconsistencias. Revisar mapeos de traducción.\n")
    
    conn.close()


def main():
    """Función principal."""
    db_path = Path("c:/desarrollo/fondos/db/fondos.sqlite")
    
    if not db_path.exists():
        print(f"ERROR: Base de datos no encontrada en {db_path}")
        sys.exit(1)
    
    # Backup automático
    backup_path = db_path.parent / f"fondos_backup_{datetime.now():%Y%m%d_%H%M%S}.sqlite"
    print(f"\n{'='*80}")
    print(f"CREANDO BACKUP")
    print(f"{'='*80}")
    print(f"Origen: {db_path}")
    print(f"Backup: {backup_path}")
    shutil.copy2(db_path, backup_path)
    print("✓ Backup creado\n")
    
    # Verificación pre-migración
    print(f"{'='*80}")
    print("ESTADO PRE-MIGRACIÓN")
    print(f"{'='*80}")
    verify_homogeneity(db_path)
    
    # Dry run primero
    normalize_languages(db_path, dry_run=True)
    
    # Confirmación
    if '--apply' in sys.argv:
        print(f"\n{'='*80}")
        print("APLICANDO CAMBIOS A LA BASE DE DATOS...")
        print(f"{'='*80}\n")
        
        affected = normalize_languages(db_path, dry_run=False)
        
        # Verificación post-migración
        verify_homogeneity(db_path)
        
        print(f"{'='*80}")
        print(f"✓ MIGRACIÓN COMPLETADA")
        print(f"{'='*80}")
        print(f"  Fondos afectados: {affected}")
        print(f"  Backup guardado en: {backup_path}")
        print(f"{'='*80}\n")
    else:
        print(f"{'='*80}")
        print("Para aplicar cambios, ejecuta:")
        print(f"  python {__file__} --apply")
        print(f"{'='*80}\n")


if __name__ == '__main__':
    main()
