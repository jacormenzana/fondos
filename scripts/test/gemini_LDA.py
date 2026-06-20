import pdfplumber
from typing import List, Dict, Tuple
import itertools

class KIIDLayoutAnalyzer:
    """
    Motor de DLA (Document Layout Analysis) para KIIDs financieros.
    Resuelve la lectura de columnas múltiples y la serialización 1D de tablas complejas.
    """
    def __init__(self, x_tolerance=10, y_tolerance=10):
        self.x_tolerance = x_tolerance
        self.y_tolerance = y_tolerance

    def process_page(self, page: pdfplumber.page.Page) -> str:
        """
        Procesa una página completa: extrae tablas serializadas y texto en columnas,
        combinándolo todo en un flujo 1D coherente.
        """
        # 1. Encontrar y procesar tablas
        tables_info = self._extract_and_serialize_tables(page)
        
        # 2. Extraer palabras que NO estén dentro de las bounding boxes de las tablas
        table_bboxes = [t['bbox'] for t in tables_info]
        words = page.extract_words(x_tolerance=self.x_tolerance, y_tolerance=self.y_tolerance)
        text_words = [w for w in words if not self._is_in_any_bbox(w, table_bboxes)]
        
        # 3. Agrupar palabras restantes en bloques/párrafos (DLA para columnas)
        text_blocks = self._cluster_into_blocks(text_words)
        
        # 4. Ensamblar el documento final ordenando bloques espaciales y tablas
        # Ordenamos los elementos (bloques de texto y tablas) de arriba hacia abajo.
        # Si están en la misma altura (Y), de izquierda a derecha.
        all_elements = text_blocks + tables_info
        all_elements.sort(key=lambda e: (round(e['top'] / 20) * 20, e['x0']))
        
        final_text = []
        for elem in all_elements:
            if elem['type'] == 'table':
                final_text.append(f"\n--- [INICIO TABLA] ---\n{elem['text']}\n--- [FIN TABLA] ---\n")
            else:
                final_text.append(elem['text'])
                
        return "\n\n".join(final_text)

    def _extract_and_serialize_tables(self, page: pdfplumber.page.Page) -> List[Dict]:
        """
        Localiza tablas, preserva la semántica 2D repitiendo encabezados en 1D.
        """
        tables_info = []
        found_tables = page.find_tables()
        
        for table_obj in found_tables:
            bbox = table_obj.bbox
            extracted_table = table_obj.extract()
            
            if not extracted_table or len(extracted_table) < 2:
                continue
                
            # Asumimos la primera fila como cabecera jerárquica
            headers = [str(h).strip().replace('\n', ' ') if h else "" for h in extracted_table[0]]
            
            serialized_rows = []
            for row in extracted_table[1:]:
                row_texts = [str(cell).strip().replace('\n', ' ') if cell else "" for cell in row]
                # Ignorar filas totalmente vacías
                if not any(row_texts):
                    continue
                    
                # El primer elemento suele ser el nombre de la fila (ej. "Escenario de tensión")
                row_header = row_texts[0] if row_texts[0] else "Valor"
                
                # Serializar preservando semántica: [Fila] | [Columna] -> [Valor]
                for i in range(1, len(row_texts)):
                    col_header = headers[i] if i < len(headers) and headers[i] else f"Columna_{i}"
                    cell_val = row_texts[i]
                    if cell_val:
                        serialized_rows.append(f"{row_header} || {col_header} : {cell_val}")
            
            tables_info.append({
                'type': 'table',
                'top': bbox[1],
                'x0': bbox[0],
                'bbox': bbox,
                'text': "\n".join(serialized_rows)
            })
            
        return tables_info

    def _cluster_into_blocks(self, words: List[Dict]) -> List[Dict]:
        """
        Agrupa palabras sueltas en párrafos y columnas basándose en coordenadas espaciales.
        Resuelve el problema de las columnas paralelas.
        """
        if not words:
            return []
            
        # Agrupar por líneas (tolerancia en Y)
        words.sort(key=lambda w: w['top'])
        lines = []
        current_line = [words[0]]
        
        for word in words[1:]:
            if abs(word['top'] - current_line[-1]['top']) <= self.y_tolerance:
                current_line.append(word)
            else:
                lines.append(current_line)
                current_line = [word]
        lines.append(current_line)
        
        # Ordenar cada línea de izquierda a derecha
        for line in lines:
            line.sort(key=lambda w: w['x0'])
            
        # Agrupar líneas en bloques verticales (columnas) si se solapan en X y están cerca en Y
        blocks = []
        for line in lines:
            line_text = " ".join(w['text'] for w in line)
            line_x0 = line[0]['x0']
            line_top = min(w['top'] for w in line)
            line_bottom = max(w['bottom'] for w in line)
            
            placed = False
            for block in blocks:
                # Si la línea cae verticalmente justo debajo del bloque y comparte márgenes X (tolerancia de columna)
                if (line_top - block['bottom'] < 30) and abs(line_x0 - block['x0']) < 50:
                    block['text'] += "\n" + line_text
                    block['bottom'] = line_bottom
                    placed = True
                    break
            
            if not placed:
                blocks.append({
                    'type': 'text',
                    'top': line_top,
                    'bottom': line_bottom,
                    'x0': line_x0,
                    'text': line_text
                })
                
        return blocks

    def _is_in_any_bbox(self, word: Dict, bboxes: List[Tuple]) -> bool:
        """Verifica si una palabra cae dentro de las coordenadas de alguna tabla."""
        wx0, wtop, wx1, wbottom = word['x0'], word['top'], word['x1'], word['bottom']
        for (bx0, btop, bx1, bbottom) in bboxes:
            # Lógica de intersección de cajas
            if (wx0 >= bx0 - 2 and wx1 <= bx1 + 2 and 
                wtop >= btop - 2 and wbottom <= bbottom + 2):
                return True
        return False

# =========================================================
# Ejemplo de integración en tu función extract_text_from_pdf_bytes
# =========================================================
def extract_text_from_pdf_bytes_dla(pdf_bytes: bytes) -> str:
    from io import BytesIO
    text_parts = []
    analyzer = KIIDLayoutAnalyzer(x_tolerance=3, y_tolerance=3)
    
    with BytesIO(pdf_bytes) as b:
        with pdfplumber.open(b) as pdf:
            for i, page in enumerate(pdf.pages):
                if i >= 3: # Tu MAX_PDF_PAGES
                    break
                try:
                    # Usamos nuestro analizador en lugar de extract_text plano
                    structured_text = analyzer.process_page(page)
                    if structured_text:
                        text_parts.append(structured_text)
                except Exception as e:
                    print(f"Error procesando DLA en página {i}: {e}")
                    continue
                    
    return "\n\n=== NUEVA PÁGINA ===\n\n".join(text_parts)