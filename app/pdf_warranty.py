import io
import qrcode
import requests
from functools import lru_cache
from pypdf import PdfReader, PdfWriter
from reportlab.pdfgen import canvas
from reportlab.lib.pagesizes import A4
from reportlab.lib.utils import ImageReader

# Đường dẫn phôi PDF mẫu trên Supabase Storage
PDF_TEMPLATE_URL = "https://ejbillnpxxiubbiyyaxn.supabase.co/storage/v1/object/public/assets/Phieu_Bao_Hanh.pdf"


@lru_cache(maxsize=1)
def get_pdf_template_bytes() -> bytes:
    """
    Tải và lưu tạm (cache) phôi PDF mẫu từ Supabase Storage trong RAM.
    Chỉ thực hiện tải 1 lần duy nhất khi ứng dụng khởi động hoặc trong lần gọi đầu tiên.
    """
    try:
        response = requests.get(PDF_TEMPLATE_URL, timeout=10)
        response.raise_for_status()
        return response.content
    except Exception as e:
        print(f"❌ Lỗi khi tải phôi PDF mẫu từ Supabase: {e}")
        raise e


def generate_qr_code_bytes(data_str: str) -> bytes:
    """Tạo ảnh mã QR Code dạng PNG bytes"""
    qr = qrcode.QRCode(
        version=1,
        error_correction=qrcode.constants.ERROR_CORRECT_M,
        box_size=8,
        border=2,
    )
    qr.add_data(data_str)
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white")
    
    img_byte_arr = io.BytesIO()
    img.save(img_byte_arr, format='PNG')
    return img_byte_arr.getvalue()


def create_warranty_pdf(data: dict) -> tuple[bytes, bytes]:
    """
    Hàm chính nhận thông tin phiếu bảo hành và xuất file PDF cùng ảnh QR Code.
    
    Dữ liệu `data` đầu vào kỳ vọng:
    {
        'customer_name': 'Nguyễn Văn A',
        'phone_number': '0901234567',
        'purchase_date': '15/03/2026',
        'model': 'Canon ix6770',
        'serial': 'AGWH12345',
        'warranty': 'Cơ máy: 12 tháng hoặc 10.000 trang\nĐầu phun: 12 tháng'
    }
    
    Trả về: (pdf_bytes, qr_png_bytes)
    """
    serial = data.get("serial", "") or "NoSerial"
    
    # 1. Tạo QR Code liên kết đến trang tra cứu bảo hành
    lookup_url = f"https://center-hub.onrender.com/warranty/lookup?q={serial}"
    qr_png_bytes = generate_qr_code_bytes(lookup_url)

    # 2. Đọc phôi PDF từ Supabase Storage (qua RAM)
    template_bytes = get_pdf_template_bytes()
    template_reader = PdfReader(io.BytesIO(template_bytes))
    writer = PdfWriter()

    # 3. Tạo layer đè văn bản bằng ReportLab
    packet = io.BytesIO()
    can = canvas.Canvas(packet, pagesize=A4)
    
    # Tùy chỉnh Font chữ (Căn chỉnh tọa độ x, y theo khung mẫu của phôi)
    can.setFont("Helvetica-Bold", 10)
    
    x_start = 140  # Vị trí lề trái nội dung
    
    # Vẽ các dòng thông tin khách hàng & thiết bị
    can.drawString(x_start, 680, str(data.get("customer_name", "")))
    can.drawString(x_start, 655, str(data.get("phone_number", "")))
    can.drawString(x_start, 630, str(data.get("model", "")))
    can.drawString(x_start, 605, str(serial))
    can.drawString(x_start, 580, str(data.get("purchase_date", "")))

    # Vẽ thông tin chính sách bảo hành (xử lý xuống dòng)
    warranty_text = str(data.get("warranty", ""))
    y_pos = 555
    for line in warranty_text.split('\n'):
        if line.strip():
            can.drawString(x_start, y_pos, line.strip())
            y_pos -= 16

    # (Tùy chọn) Chèn trực tiếp góc mã QR vào phôi PDF
    try:
        qr_img = ImageReader(io.BytesIO(qr_png_bytes))
        can.drawImage(qr_img, x=430, y=580, width=90, height=90)
    except Exception as e:
        print(f"⚠️ Không thể vẽ QR lên PDF: {e}")

    can.save()

    # 4. Gộp layer dữ liệu mới vừa tạo vào trang 1 của phôi PDF
    packet.seek(0)
    overlay_pdf = PdfReader(packet)
    
    page_1 = template_reader.pages[0]
    if len(overlay_pdf.pages) > 0:
        page_1.merge_page(overlay_pdf.pages[0])
    writer.add_page(page_1)

    # Giữ nguyên các trang còn lại của phôi nếu phôi có nhiều trang
    for i in range(1, len(template_reader.pages)):
        writer.add_page(template_reader.pages[i])

    # 5. Xuất kết quả PDF dạng bytes
    pdf_output_stream = io.BytesIO()
    writer.write(pdf_output_stream)
    pdf_bytes = pdf_output_stream.getvalue()

    return pdf_bytes, qr_png_bytes