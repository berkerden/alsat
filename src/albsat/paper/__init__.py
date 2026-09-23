"""Kâğıt işlem modu (SPEC.md §4.7, Faz 4): canlı veriyle, gerçek para olmadan.

Bu paket borsaya **hiçbir emir göndermez**. Emirler, dolumlar ve bakiyeler
yalnızca yerel SQLite dosyasında yaşar. Canlı fiyat herkese açık piyasa
verisinden gelir; API anahtarı kullanılmaz.
"""
