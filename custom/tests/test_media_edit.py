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
