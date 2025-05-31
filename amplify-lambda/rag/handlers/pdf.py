#Copyright (c) 2024 Vanderbilt University  
#Authors: Jules White, Allen Karns, Karely Rodriguez, Max Moundas

import io
import pypdfium2 as pdfium
import re
from collections import Counter

from rag.handlers.text import TextExtractionHandler


class PDFHandler(TextExtractionHandler):
    def extract_text(self, file_content, encoding):
        with io.BytesIO(file_content) as f:
            pdf = pdfium.PdfDocument(f)

            chunks = []
            num_pages = len(pdf)  # Get the number of pages in the document

            for page_index in range(num_pages):
                page_number = page_index + 1  # Convert zero-based index to one-based page numbering for display

                page = pdf[page_index]  # Load the page using zero-based indexing
                textpage = page.get_textpage()

                # Extract text from the whole page
                text = textpage.get_text_range()

                if not text:
                    continue

                chunk = {
                        'content': text,
                        'tokens': self.num_tokens_from_string(text),
                        'location': {'page_number': page_number},
                        'canSplit': True
                }
                chunks.append(chunk)

                # pypdfium2 might not require explicit page close, depending on API implementation details
                # If needed: page.close()

            # Since pypdfium2 loads the whole document, ensure to close it to free resources
            pdf.close()

            return chunks


    def _analyze_text_quality(self, text):
        """Analyze text quality to detect OCR artifacts"""
        if not text or len(text.strip()) < 10:
            return {'quality_score': 0.0, 'ocr_artifacts': []}
        
        artifacts = []
        quality_score = 1.0
        
        # Check for common OCR errors
        # 1. Excessive special characters or garbled text
        special_char_ratio = len(re.findall(r'[^\w\s\.\,\!\?\;\:\-\(\)]', text)) / len(text)
        if special_char_ratio > 0.1:
            artifacts.append('high_special_chars')
            quality_score -= 0.2
        
        # 2. Broken words (single characters with spaces)
        broken_words = len(re.findall(r'\b\w\s+\w\s+\w\b', text))
        if broken_words > len(text.split()) * 0.1:
            artifacts.append('broken_words')
            quality_score -= 0.3
        
        # 3. Number/letter confusion (common OCR errors)
        confusion_patterns = [r'[0O]{2,}', r'[1Il]{3,}', r'[5S]{2,}']
        for pattern in confusion_patterns:
            if len(re.findall(pattern, text)) > 3:
                artifacts.append('char_confusion')
                quality_score -= 0.2
                break
        
        # 4. Excessive line breaks or formatting issues
        line_break_ratio = text.count('\n') / len(text) if len(text) > 0 else 0
        if line_break_ratio > 0.05:
            artifacts.append('formatting_issues')
            quality_score -= 0.1
        
        # 5. Check for coherent sentences
        sentences = re.split(r'[.!?]+', text)
        short_sentences = sum(1 for s in sentences if len(s.strip().split()) < 3)
        if len(sentences) > 0 and short_sentences / len(sentences) > 0.5:
            artifacts.append('fragmented_sentences')
            quality_score -= 0.2
        
        return {
            'quality_score': max(0.0, quality_score),
            'ocr_artifacts': artifacts
        }
    
    def _analyze_layout_and_fonts(self, page):
        """Analyze page layout and font characteristics"""
        try:
            textpage = page.get_textpage()
            
            # Get text objects for font analysis
            char_count = textpage.count_chars()
            font_sizes = []
            
            for i in range(min(char_count, 100)):  # Sample first 100 characters
                try:
                    font_size = textpage.get_font_size(i)
                    if font_size > 0:
                        font_sizes.append(font_size)
                except:
                    continue
            
            # Analyze font diversity
            if font_sizes:
                font_size_variety = len(set(font_sizes))
                avg_font_size = sum(font_sizes) / len(font_sizes)
            else:
                font_size_variety = 0
                avg_font_size = 0
            
            # Get page dimensions for layout analysis
            page_width = page.get_width()
            page_height = page.get_height()
            
            return {
                'font_variety': font_size_variety,
                'avg_font_size': avg_font_size,
                'page_dimensions': (page_width, page_height),
                'has_font_info': len(font_sizes) > 0
            }
            
        except Exception as e:
            print(f"Error analyzing layout: {str(e)}")
            return {
                'font_variety': 0,
                'avg_font_size': 0,
                'page_dimensions': (0, 0),
                'has_font_info': False
            }
    
    def _count_images_advanced(self, page):
        """More robust image detection"""
        image_count = 0
        image_area = 0
        
        try:
            # Get page dimensions
            page_width = page.get_width()
            page_height = page.get_height()
            total_page_area = page_width * page_height
            
            # Count images and calculate their area
            objects = page.get_objects()
            for obj in objects:
                if obj.get_type() == pdfium.FPDF_PAGEOBJ_IMAGE:
                    image_count += 1
                    # Try to get image dimensions
                    try:
                        bbox = obj.get_bounds()
                        if bbox and len(bbox) == 4:
                            img_width = abs(bbox[2] - bbox[0])
                            img_height = abs(bbox[3] - bbox[1])
                            image_area += img_width * img_height
                    except:
                        # Fallback: assume average image size
                        image_area += total_page_area * 0.1
            
            image_coverage = image_area / total_page_area if total_page_area > 0 else 0
            
            return {
                'image_count': image_count,
                'image_coverage': min(1.0, image_coverage),
                'has_large_images': image_coverage > 0.3
            }
            
        except Exception as e:
            print(f"Error counting images: {str(e)}")
            return {
                'image_count': 0,
                'image_coverage': 0.0,
                'has_large_images': False
            }

    def is_ocr_or_image_heavy_pdf(self, file_content):
        """
        Enhanced analysis to determine if PDF is OCR-based or contains images
        that MarkItDown might not handle well.
        
        Returns:
            dict: Comprehensive analysis results
        """
        try:
            with io.BytesIO(file_content) as f:
                pdf = pdfium.PdfDocument(f)
                
                total_pages = len(pdf)
                if total_pages == 0:
                    pdf.close()
                    return self._get_default_analysis()
                
                # Adaptive sampling: more pages for better accuracy
                sample_size = min(total_pages, max(5, total_pages // 3))
                sample_pages = list(range(0, total_pages, max(1, total_pages // sample_size)))[:sample_size]
                
                # Metrics collection
                text_metrics = []
                quality_scores = []
                artifact_counts = Counter()
                image_metrics = []
                layout_metrics = []
                
                for page_index in sample_pages:
                    try:
                        page = pdf[page_index]
                        textpage = page.get_textpage()
                        
                        # Extract and analyze text
                        text = textpage.get_text_range()
                        text_length = len(text.strip()) if text else 0
                        text_metrics.append(text_length)
                        
                        # Analyze text quality
                        if text:
                            quality_analysis = self._analyze_text_quality(text)
                            quality_scores.append(quality_analysis['quality_score'])
                            for artifact in quality_analysis['ocr_artifacts']:
                                artifact_counts[artifact] += 1
                        else:
                            quality_scores.append(0.0)
                        
                        # Analyze images
                        image_analysis = self._count_images_advanced(page)
                        image_metrics.append(image_analysis)
                        
                        # Analyze layout and fonts
                        layout_analysis = self._analyze_layout_and_fonts(page)
                        layout_metrics.append(layout_analysis)
                        
                    except Exception as e:
                        print(f"Error analyzing page {page_index}: {str(e)}")
                        continue
                
                pdf.close()
                
                # Calculate comprehensive metrics
                analysis = self._calculate_comprehensive_metrics(
                    text_metrics, quality_scores, artifact_counts, 
                    image_metrics, layout_metrics, total_pages
                )
                
                return analysis
                
        except Exception as e:
            print(f"Error in comprehensive PDF analysis: {str(e)}")
            return self._get_default_analysis()
    
    def _calculate_comprehensive_metrics(self, text_metrics, quality_scores, 
                                       artifact_counts, image_metrics, layout_metrics, total_pages):
        """Calculate final OCR detection metrics"""
        
        # Text analysis
        avg_text_per_page = sum(text_metrics) / len(text_metrics) if text_metrics else 0
        pages_with_minimal_text = sum(1 for t in text_metrics if t < 100)
        minimal_text_ratio = pages_with_minimal_text / len(text_metrics) if text_metrics else 0
        
        # Quality analysis
        avg_quality_score = sum(quality_scores) / len(quality_scores) if quality_scores else 1.0
        has_quality_issues = avg_quality_score < 0.7
        
        # Image analysis
        total_images = sum(img['image_count'] for img in image_metrics)
        avg_image_coverage = sum(img['image_coverage'] for img in image_metrics) / len(image_metrics) if image_metrics else 0
        has_large_images = any(img['has_large_images'] for img in image_metrics)
        
        # Layout analysis
        font_variety_scores = [layout['font_variety'] for layout in layout_metrics if layout['has_font_info']]
        avg_font_variety = sum(font_variety_scores) / len(font_variety_scores) if font_variety_scores else 0
        low_font_variety = avg_font_variety < 2  # Suggests limited formatting
        
        # Enhanced OCR detection logic
        ocr_indicators = []
        confidence = 0.0
        
        # Text-based indicators
        if avg_text_per_page < 150:
            ocr_indicators.append('very_low_text')
            confidence += 0.3
        elif avg_text_per_page < 300:
            ocr_indicators.append('low_text')
            confidence += 0.2
        
        if minimal_text_ratio > 0.6:
            ocr_indicators.append('many_empty_pages')
            confidence += 0.25
        
        # Quality-based indicators
        if has_quality_issues:
            ocr_indicators.append('text_quality_issues')
            confidence += 0.3
        
        if artifact_counts:
            ocr_indicators.append('ocr_artifacts_detected')
            confidence += 0.2
        
        # Image-based indicators
        if avg_image_coverage > 0.4:
            ocr_indicators.append('high_image_coverage')
            confidence += 0.2
        
        if has_large_images and avg_text_per_page < 400:
            ocr_indicators.append('large_images_with_low_text')
            confidence += 0.25
        
        # Layout-based indicators
        if low_font_variety and avg_text_per_page < 500:
            ocr_indicators.append('simple_layout_low_text')
            confidence += 0.15
        
        # Special case: scanned documents (high image coverage + low quality text)
        if avg_image_coverage > 0.3 and avg_quality_score < 0.5:
            ocr_indicators.append('likely_scanned_document')
            confidence += 0.4
        
        confidence = min(1.0, confidence)
        is_ocr = confidence > 0.5
        
        return {
            'is_ocr': is_ocr,
            'confidence': confidence,
            'has_images': total_images > 0,
            'ocr_indicators': ocr_indicators,
            'metrics': {
                'avg_text_per_page': avg_text_per_page,
                'text_quality_score': avg_quality_score,
                'image_coverage': avg_image_coverage,
                'total_images': total_images,
                'font_variety': avg_font_variety,
                'artifact_types': list(artifact_counts.keys())
            },
            'recommendation': self._get_processing_recommendation(is_ocr, confidence, ocr_indicators)
        }
    
    def _get_processing_recommendation(self, is_ocr, confidence, indicators):
        """Provide processing recommendations based on analysis"""
        if confidence > 0.8:
            return "strongly_recommend_ocr_processing"
        elif confidence > 0.6:
            return "recommend_ocr_processing"
        elif confidence > 0.3:
            return "consider_ocr_processing"
        else:
            return "standard_processing_sufficient"
    
    def _get_default_analysis(self):
        """Return default analysis when detection fails"""
        return {
            'is_ocr': False,
            'confidence': 0.0,
            'has_images': False,
            'ocr_indicators': [],
            'metrics': {},
            'recommendation': 'standard_processing_sufficient'
        }

