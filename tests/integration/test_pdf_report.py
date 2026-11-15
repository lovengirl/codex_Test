"""HTTP 없이 함수 입력과 반환값을 검증한다."""


from io import BytesIO


from pypdf import PdfReader


from dashboard_exports import dashboard_report


def test_PDF여러페이지에서도_전체월과_한글헤더유지():
    data = {'metrics': {'customer_count': 1, 'order_count': 120, 'total_sales': 120000, 'average_order': 1000},
            'monthly_sales': [{'month': f'{2000+index//12}-{index%12+1:02}', 'sales': 1000} for index in range(120)]}
    pdf = PdfReader(BytesIO(dashboard_report(data, 'pdf')))
    assert len(pdf.pages) > 1
    assert all('구분' in page.extract_text() for page in pdf.pages)
    assert '2009-12' in pdf.pages[-1].extract_text()

