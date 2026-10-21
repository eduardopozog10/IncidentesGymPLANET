"""Operational catalogue and authenticated original downloads; no public storage URL."""
from django import forms
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Q
from django.http import Http404, HttpResponse, HttpResponseRedirect
from django.shortcuts import get_object_or_404
from django.urls import reverse
from django.utils.http import content_disposition_header
from incidents.dashboard_views import OperationalView
from incidents.models import RequestProduct, ProductAlias, RequestDocument
from incidents.services.gymplanet_conversation import normalize
from incidents.services.request_documents import original_bytes, DocumentInputError


class ProductForm(forms.ModelForm):
    synonyms = forms.CharField(label='Sinónimos (uno por línea)',required=False,widget=forms.Textarea(attrs={'rows':4}),max_length=4000)

    class Meta:
        model = RequestProduct
        fields = ['code','name','presentation','purchase_unit','is_active']
        labels = {'code':'Código','is_active':'Activo'}

    def __init__(self,*args,**kwargs):
        super().__init__(*args,**kwargs)
        if self.instance.pk and not self.is_bound:
            self.fields['synonyms'].initial = '\n'.join(self.instance.aliases.values_list('name',flat=True))

    def clean_synonyms(self):
        values = [line.strip() for line in self.cleaned_data['synonyms'].splitlines() if line.strip()]
        if len(values)>30 or any(not normalize(v) or len(v)>120 for v in values):
            raise forms.ValidationError('Usa hasta 30 nombres de hasta 120 caracteres.')
        return list(dict.fromkeys(values))


class ProductCatalogView(OperationalView):
    template_name = 'incidents/product_catalog.html'
    title = 'Productos de solicitudes'
    section = 'products'

    def get_context_data(self,**kwargs):
        context = super().get_context_data(**kwargs)
        product = get_object_or_404(RequestProduct,pk=self.kwargs['pk']) if self.kwargs.get('pk') else None
        rows = RequestProduct.objects.prefetch_related('aliases').all()
        search = self.request.GET.get('search','')[:120]
        if search:
            rows = rows.filter(Q(name__icontains=search)|Q(code__icontains=search)|Q(aliases__name__icontains=search)).distinct()
        active = self.request.GET.get('active','')
        if active in ('yes','no'):
            rows = rows.filter(is_active=active=='yes')
        context.update(form=kwargs.get('form') or ProductForm(instance=product),product=product,
            page_obj=Paginator(rows,20).get_page(self.request.GET.get('page')),search=search,active=active)
        return context

    @transaction.atomic
    def post(self,request,*args,**kwargs):
        product = get_object_or_404(RequestProduct.objects.select_for_update(),pk=kwargs['pk']) if kwargs.get('pk') else None
        form = ProductForm(request.POST,instance=product)
        if not form.is_valid():
            return self.render_to_response(self.get_context_data(form=form),status=400)
        product = form.save()
        values = {normalize(name):name for name in form.cleaned_data['synonyms']}
        product.aliases.exclude(normalized__in=values).delete()
        for key,name in values.items():
            ProductAlias.objects.update_or_create(product=product,normalized=key,defaults={'name':name})
        return HttpResponseRedirect(reverse('request-products'))


class RequestDocumentDownload(OperationalView):
    def get(self,request,*args,**kwargs):
        # Current operations permission is global; no branch-local role gets implicit access.
        document = get_object_or_404(RequestDocument,pk=kwargs['pk'],report__isnull=False)
        try:
            content = original_bytes(document)
        except (OSError,DocumentInputError,ValueError):
            raise Http404('No se puede verificar el original.') from None
        response = HttpResponse(content,content_type=document.media_type)
        response['Content-Disposition'] = content_disposition_header(True,document.original_name)
        response['X-Content-Type-Options'] = 'nosniff'
        response['Cache-Control'] = 'private, no-store'
        return response
