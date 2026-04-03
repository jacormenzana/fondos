# -*- coding: utf-8 -*-
"""
Created on Fri Jan  2 20:40:49 2026

@author: Administrador
"""

import logging

def get_logger():
    logging.basicConfig(
        filename="logs/pipeline.log",
        level=logging.INFO,
        format="%(asctime)s | %(levelname)s | %(message)s"
    )
    return logging.getLogger("pipeline")