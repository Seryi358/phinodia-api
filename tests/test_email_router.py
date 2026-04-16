def test_build_delivery_email_video_8s():
    from app.services.gmail import build_delivery_email
    subject, html = build_delivery_email("Crema Glow", "video_8s", "https://cdn.kie.ai/video.mp4")
    assert "Crema Glow" in subject
    assert "Video de 8 segundos" in subject
    assert "https://cdn.kie.ai/video.mp4" in html
    assert "Ley 1581" in html

def test_build_delivery_email_video_15s():
    from app.services.gmail import build_delivery_email
    subject, html = build_delivery_email("Crema Glow", "video_15s", "https://cdn.kie.ai/video.mp4")
    assert "Video de 15 segundos" in subject

def test_build_delivery_email_video_30s():
    from app.services.gmail import build_delivery_email
    subject, html = build_delivery_email("Crema Glow", "video_30s", "https://cdn.kie.ai/video.mp4")
    assert "Video de 30 segundos" in subject

def test_build_delivery_email_image():
    from app.services.gmail import build_delivery_email
    subject, html = build_delivery_email("Serum X", "image", "https://cdn.kie.ai/image.jpg")
    assert "Imagen de producto" in subject

def test_build_delivery_email_landing():
    from app.services.gmail import build_delivery_email
    subject, html = build_delivery_email("Cafe Premium", "landing_page", "https://app.phinodia.com/download/abc")
    assert "Landing Page" in subject
