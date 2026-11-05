"""Mask contract, validation and authenticated reference submission."""
import base64,io,time
from PIL import Image,ImageDraw,ImageChops
from fastapi import HTTPException
import pytest
from hub_console import chat_media as m

def encoded(im):
    b=io.BytesIO();im.save(b,format='PNG');return base64.b64encode(b.getvalue()).decode()

def test_inpaint_preserves_every_unselected_pixel_at_original_dimensions(tmp_path):
    original=Image.new('RGB',(123,95));original.putdata([(i%256,(i*3)%256,(i*7)%256) for i in range(123*95)])
    mask=Image.new('L',original.size);ImageDraw.Draw(mask).ellipse((33,21,88,80),fill=255)
    original.save(tmp_path/'source.png');mask.save(tmp_path/'mask.png');generated=Image.new('RGB',(64,64),'red')
    m.composite_result(tmp_path,base64.b64decode(encoded(generated)),{'mode':'inpaint'})
    result=Image.open(tmp_path/'image.png');assert result.size==original.size
    changed=0
    for a,b,k in zip(original.getdata(),result.getdata(),mask.getdata()):
        if k==0:assert a==b
        elif a!=b:changed+=1
    assert changed>1000

def test_input_validation_and_mask_semantics():
    source=Image.new('RGB',(64,80),'blue');black=Image.new('L',source.size)
    for body in [{'mode':'other'},{'mode':'reference','source_base64':'garbage'},{'mode':'inpaint','source_base64':encoded(source),'mask_base64':encoded(black)},{'mode':'inpaint','source_base64':encoded(source),'mask_base64':encoded(Image.new('L',(4,4),255))}]:
        with pytest.raises(HTTPException):m.prepared_inputs(body)
    white=Image.new('L',source.size,255)
    mode,src,mask=m.prepared_inputs({'mode':'inpaint','source_base64':encoded(source),'mask_base64':encoded(white)})
    assert src.size==(64,80) and mask.getextrema()==(255,255)
    assert m.input_digest(mode,src,mask)!=m.input_digest('reference',src,None)

def test_edit_graph_uses_reference_and_mask_without_arbitrary_graph():
    j={'model':'sdxl-lightning','seed':1,'strength':.7}
    graph=m.edit_workflow(j,'red hat','owned.png','mask.png')
    assert graph['13']['class_type']=='SetLatentNoiseMask'
    assert graph['12']['inputs']['channel']=='red'
    assert graph['5']['inputs']['denoise']==.7
    assert m.edit_workflow(j,'hat','owned.png')['5']['inputs']['denoise']==.7


def test_phone_photo_over_old_limit_preserves_exif_oriented_source():
    # The user's valid 4928x6560 JPEG was rejected as corrupt by the old 16 MP cap.
    image=Image.new('RGB',(4928,6560),'navy');buffer=io.BytesIO()
    exif=Image.Exif();exif[274]=6
    image.save(buffer,'JPEG',exif=exif);image.close()
    source=m.decode_image(base64.b64encode(buffer.getvalue()).decode())
    assert source.size==(6560,4928) and source.mode=='RGB'
    assert source.getpixel((0,0))[2]>=125
    source.close()


def test_input_pixel_guard_rejects_before_full_decode(monkeypatch):
    class Header:
        width=9000;height=9000;size=(9000,9000);format='PNG'
        def __enter__(self):return self
        def __exit__(self,*args):pass
        def load(self):raise AssertionError('oversized image must not decode')
    monkeypatch.setattr(m.Image,'open',lambda *_:Header())
    with pytest.raises(HTTPException,match='64 мегапикселя'):
        m.decode_image(base64.b64encode(b'header').decode())


def test_unsupported_image_and_corruption_have_distinct_errors():
    buffer=io.BytesIO();Image.new('RGB',(16,16)).save(buffer,'GIF')
    with pytest.raises(HTTPException,match='Поддерживаются PNG'):
        m.decode_image(base64.b64encode(buffer.getvalue()).decode())
    with pytest.raises(HTTPException,match='повреждён'):
        m.decode_image(base64.b64encode(b'not a photo').decode())


@pytest.mark.asyncio
async def test_large_reference_model_upload_is_bounded_and_original_untouched(tmp_path):
    import httpx
    source=Image.new('RGB',(4928,6560),'navy');source.save(tmp_path/'source.png');source.close()
    original=(tmp_path/'source.png').read_bytes();seen=[]
    class Upload:
        async def post(self,url,files,data):
            with Image.open(io.BytesIO(files['image'][1])) as im:seen.append(im.size)
            return httpx.Response(200,json={'name':files['image'][0]},request=httpx.Request('POST','http://local/upload/image'))
    await m.upload_inputs(Upload(),tmp_path,{'model':'flux-klein4b','mode':'reference','id':'test'})
    assert seen and max(seen[0])<=768
    assert (tmp_path/'source.png').read_bytes()==original
