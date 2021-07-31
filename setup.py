#!/usr/bin/env python
if __name__ == '__main__':
  from setuptools import setup
  from setuptools.extension import Extension
  from Cython.Build import cythonize
  import subprocess as sp

  # this should create the file rbf/_version.py
  sp.call(['python', 'make_version.py'])
  version_info = {}
  with open('rbf/_version.py', 'r') as fb:
    exec(fb.read(), version_info)
  
  ext = []
  ext += [Extension(name='rbf.poly',
                    sources=['rbf/poly.pyx'])]
  ext += [Extension(name='rbf.sputils',
                    sources=['rbf/sputils.pyx'])]
  ext += [Extension(name='rbf.pde.halton',
                    sources=['rbf/pde/halton.pyx'])]
  ext += [Extension(name='rbf.pde.geometry',
                    sources=['rbf/pde/geometry.pyx'])]
  ext += [Extension(name='rbf.pde.sampling',
                    sources=['rbf/pde/sampling.pyx'])]
  setup(name='RBF',
        version=version_info['__version__'],
        description='Package containing the tools necessary for radial basis '
                    'function (RBF) applications',
        author='Trever Hines',
        author_email='treverhines@gmail.com',
        url='www.github.com/treverhines/RBF',
        packages=['rbf', 'rbf.pde'],
        install_requires=[
          'numpy>=1.10',
          'scipy',
          'sympy',
          'cython',
          'rtree',
          'scikit-sparse>=0.4.2',
        ],
        ext_modules=cythonize(ext),
        license='MIT')
