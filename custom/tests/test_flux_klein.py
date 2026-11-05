"""Flow instruction-edit graph must condition on reference, not corrupt it via img2img."""
from hub_console import chat_media as m

def test_klein_distilled_workflow_uses_quant_encoder_and_correct_flux_schedule():
    g=m.klein_workflow('two runners',123,512,768)
    assert g['1']['class_type']=='UnetLoaderGGUF'
    assert g['2']['inputs']=={'clip_name':'Qwen3-4B-Q4_K_M.gguf','type':'flux2'}
    assert g['11']['inputs']=={'steps':4,'width':512,'height':768}
    assert g['6']['inputs']['cfg']==1.0
    assert g['10']['class_type']=='EmptyFlux2LatentImage'
    assert g['9']['inputs']['images']==['14',0]

def test_reference_and_mask_edit_generate_new_latent_conditioned_on_source():
    for mode in ['reference','inpaint']:
        g=m.edit_workflow({'model':'flux-klein4b','seed':1,'mode':mode},'make jacket green','source.png','mask.png' if mode=='inpaint' else None)
        assert g['21']['class_type']=='VAEEncodeTiled'
        assert g['22']['class_type']=='ReferenceLatent'
        assert g['6']['inputs']['positive']==['22',0]
        assert g['6']['inputs']['negative']==['23',0]
        assert g['12']['inputs']['latent_image']==['10',0]
        assert g['10']['inputs']['width']==['24',0]
        assert all(n['class_type']!='KSampler' for n in g.values())
