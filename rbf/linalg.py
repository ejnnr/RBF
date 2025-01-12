'''
Module that defines solvers for matrices that are frequently encountered in RBF
applications. Most functions in this module can take either scipy sparse
matrices or numpy arrays as input
'''
import logging
import warnings

import numpy as np
import scipy.sparse as sp
import scipy.sparse.linalg as spla
from scipy.linalg.lapack import dpotrf, dpotrs, dtrtrs, dgetrf, dgetrs

from rbf.sputils import row_norms, divide_rows

LOGGER = logging.getLogger(__name__)

try:
    from sksparse import cholmod
    HAS_CHOLMOD = True
except ImportError:
    HAS_CHOLMOD = False
    CHOLMOD_MSG = (
        'Could not import CHOLMOD. Sparse matrices will be converted to dense '
        'for all Cholesky decompositions'
        )
    LOGGER.debug(CHOLMOD_MSG)


## Wrappers for low level LAPACK functions. These are all a few microseconds
## faster than their corresponding functions in scipy.linalg
###############################################################################
def _lu(A):
    '''
    Computes the LU factorization of `A` using `dgetrf`
    '''
    if A.shape == (0, 0):
        return (np.zeros((0, 0), dtype=float), np.zeros((0,), dtype=np.int32))

    fac, piv, info = dgetrf(A)
    if info < 0:
        raise ValueError('the %s-th argument had an illegal value' % -info)
    elif info > 0:
        raise np.linalg.LinAlgError('Singular matrix')

    return fac, piv


def _cholesky(A, lower=True):
    '''
    Computes the Cholesky decomposition of `A` using `dpotrf`
    '''
    if A.shape == (0, 0):
        return np.zeros((0, 0), dtype=float)

    L, info = dpotrf(A, lower=lower)
    if info < 0:
        raise ValueError('The %s-th argument has an illegal value.' % -info)
    elif info > 0:
        raise np.linalg.LinAlgError('Matrix not positive definite')


    return L


def _solve_lu(fac, piv, b):
    '''
    Solves `Ax = b` given the LU factorization of `A` using `dgetrs`
    '''
    if any(i == 0 for i in b.shape):
        return np.zeros(b.shape, dtype=float)

    x, info = dgetrs(fac, piv, b)
    if info < 0:
        raise ValueError('the %s-th argument had an illegal value' % -info)

    return x


def _solve_cholesky(L, b, lower=True):
    '''
    Solves `Ax = b` given the Cholesky decomposition of `A` using `dpotrs`
    '''
    if any(i == 0 for i in b.shape):
        return np.zeros(b.shape, dtype=float)

    x, info = dpotrs(L, b, lower=lower)
    if info < 0:
        raise ValueError('The %s-th argument has an illegal value.' % -info)

    return x


def _solve_triangular(L, b, lower=True):
    '''
    Solves `Lx = b`  for a triangular `L` using `dtrtrs`
    '''
    if any(i == 0 for i in b.shape):
        return np.zeros(b.shape, dtype=float)

    x, info = dtrtrs(L, b, lower=lower)
    if info < 0:
        raise ValueError('The %s-th argument had an illegal value' % -info)
    elif info > 0:
        raise np.linalg.LinAlgError('Singular matrix')

    return x


#####################################################################
def as_sparse_or_array(A, dtype=None, copy=False):
    '''
    If `A` is a scipy sparse matrix then return it as a csc matrix. Otherwise,
    return it as an array.
    '''
    if sp.issparse(A):
        # This does not make a copy if A is csc, has the same dtype and copy is
        # false.
        A = sp.csc_matrix(A, dtype=dtype, copy=copy)
    else:
        A = np.array(A, dtype=dtype, copy=copy)

    return A


def as_array(A, dtype=None, copy=False):
    '''
    Return `A` as an array if it is not already. This properly handles when `A`
    is sparse.
    '''
    if sp.issparse(A):
        A = A.toarray()

    A = np.array(A, dtype=dtype, copy=copy)
    return A


class _SparseSolver:
    '''
    Sparse matrix solver using SuperLU
    '''
    def __init__(self, A):
        LOGGER.debug(
            'Computing the LU decomposition with %.2f%% nonzeros' %
            (100*A.nnz/(A.shape[0]*A.shape[1]),)
            )
        self.factor = spla.splu(A)

    def solve(self, b):
        return self.factor.solve(b)


class _DenseSolver:
    '''
    Dense matrix solver using LAPACK LU factorization
    '''
    def __init__(self, A):
        fac, piv = _lu(A)
        self.fac = fac
        self.piv = piv

    def solve(self, b):
        return _solve_lu(self.fac, self.piv, b)


class Solver:
    '''
    Sparse or dense matrix solver

    Parameters
    ----------
    A : (n, n) array or sparse matrix

    '''
    def __init__(self, A, build_inverse=False):
        A = as_sparse_or_array(A, dtype=float)
        if sp.issparse(A):
            self._solver = _SparseSolver(A)
        else:
            self._solver = _DenseSolver(A)

        if build_inverse:
            I = np.eye(A.shape[0])
            self._inverse = self._solver.solve(I)
        else:
            self._inverse = None

        self.n = A.shape[0]

    def solve(self, b):
        '''
        solves `Ax = b` for `x`

        Parameters
        ----------
        b : (n, ...) array or sparse matrix

        Returns
        -------
        (n, ...) array

        '''
        b = as_array(b, dtype=float)
        if self._inverse is not None:
            return self._inverse.dot(b)
        else:
            return self._solver.solve(b)


class _SparsePosDefSolver:
    '''
    Sparse positive definite matrix solver using CHOLMOD

    Factors the matrix as `LL^T = A`. Note that `L` is NOT necessarily a lower
    triangular matrix
    '''
    def __init__(self, A):
        LOGGER.debug(
            'Computing the Cholesky decomposition with %.2f%% nonzeros' %
            (100*A.nnz/(A.shape[0]*A.shape[1]),)
            )
        self.factor = cholmod.cholesky(
            A,
            use_long=False,
            ordering_method='default'
            )
        # store the squared diagonal components of the cholesky factorization
        self.d = self.factor.D()
        # store the permutation array, which permutes `A` such that its
        # Cholesky factorization is maximally sparse
        self.p = self.factor.P()

    def solve(self, b):
        '''
        Solves `Ax = b` for `x`
        '''
        return self.factor.solve_A(b)

    def solve_L(self, b):
        '''
        Solves `Lx = b` for `x`
        '''
        s_inv = 1.0/np.sqrt(self.d)
        if b.ndim == 2:
            # expand for broadcasting
            s_inv = s_inv[:, None]
        elif b.ndim != 1:
            raise ValueError('`b` must be a 1 or 2 dimensional array')

        out = s_inv*self.factor.solve_L(b[self.p])
        return out

    def L(self):
        '''Return the factorization `L`'''
        L = self.factor.L()
        p_inv = np.argsort(self.p)
        out = L[p_inv]
        return out

    def log_det(self):
        '''Returns the log determinant of `A`'''
        out = np.sum(np.log(self.d))
        return out


class _DensePosDefSolver:
    '''
    Dense positive definite matrix solver using LAPACK Cholesky decomposition
    '''
    def __init__(self, A):
        self.chol = _cholesky(A, lower=True)

    def solve(self, b):
        '''
        Solves the equation `Ax = b` for `x`
        '''
        return _solve_cholesky(self.chol, b, lower=True)

    def solve_L(self, b):
        '''
        Solves the equation `Lx = b` for `x`, where `L` is the Cholesky
        decomposition.
        '''
        return _solve_triangular(self.chol, b, lower=True)

    def L(self):
        '''Returns the Cholesky decomposition of `A`'''
        return self.chol

    def log_det(self):
        '''Returns the log determinant of `A`'''
        out = 2*np.sum(np.log(np.diag(self.chol)))
        return out


class PosDefSolver:
    '''
    Sparse or dense positive definite matrix solver

    Factors the positive definite matrix `A` as `LL^T = A` and provides an
    efficient method for solving `Ax = b` for `x`. Additionally provides a
    method to solve `Lx = b`, get the log determinant of `A`, and get `L`.

    Parameters
    ----------
    A : (n, n) array or sparse matrix
        Positive definite matrix

    '''
    def __init__(self, A, build_inverse=False):
        A = as_sparse_or_array(A, dtype=float)
        if sp.issparse(A):
            if not HAS_CHOLMOD:
                warnings.warn(CHOLMOD_MSG)
                self._solver = _DensePosDefSolver(A.toarray())
            else:
                self._solver = _SparsePosDefSolver(A)

        else:
            self._solver = _DensePosDefSolver(A)

        if build_inverse:
            I = np.eye(A.shape[0])
            self._inverse = self._solver.solve(I)
        else:
            self._inverse = None

        self.n = A.shape[0]

    def solve(self, b):
        '''
        solves `Ax = b` for `x`

        Parameters
        ----------
        b : (n, ...) array or sparse matrix

        Returns
        -------
        (n, ...) array

        '''
        b = as_array(b, dtype=float)
        if self._inverse is not None:
            return self._inverse.dot(b)
        else:
            return self._solver.solve(b)

    def solve_L(self, b):
        '''
        solves `Lx = b` for `x`

        Parameters
        ----------
        b : (n, ...) array or sparse matrix

        Returns
        -------
        (n, ...) array

        '''
        b = as_array(b, dtype=float)
        return self._solver.solve_L(b)

    def L(self):
        '''
        Returns the factorization `L`

        Returns
        -------
        (n, n) array or sparse matrix

        '''
        return self._solver.L()

    def log_det(self):
        '''
        Returns the log determinant of `A`

        Returns
        -------
        float

        '''
        return self._solver.log_det()


def is_positive_definite(A):
    '''
    Tests if `A` is positive definite. This is done by testing whether the
    Cholesky decomposition finishes successfully. `A` can be a sparse matrix or
    array.
    '''
    try:
        PosDefSolver(A).L()
    except (np.linalg.LinAlgError, cholmod.CholmodNotPositiveDefiniteError):
        return False

    return True


class PartitionedSolver:
    '''
    Solves the system of equations

    .. math::
        \\left[
        \\begin{array}{cc}
            A   & B \\\\
            B^T & 0 \\\\
        \\end{array}
        \\right]
        \\left[
        \\begin{array}{c}
            x \\\\
            y \\\\
        \\end{array}
        \\right]
        =
        \\left[
        \\begin{array}{c}
            a \\\\
            b \\\\
        \\end{array}
        \\right]

    for `x` and `y`. This class builds the system and then factors it with an
    LU decomposition. As opposed to `PartitionedPosDefSolver`, `A` is not
    assumed to be positive definite. `A` can be a sparse matrix or an array.
    `B` can also be a sparse matrix but it will be converted to an array.

    Parameters
    ----------
    A : (n, n) array or sparse matrix

    B : (n, p) array or sparse matrix

    '''
    def __init__(self, A, B, build_inverse=False):
        A = as_sparse_or_array(A, dtype=float)
        B = as_array(B, dtype=float)
        n, p = B.shape
        if n < p:
            raise np.linalg.LinAlgError(
              'There are fewer rows than columns in `B`. This makes the block '
              'matrix singular, and its inverse cannot be computed.'
              )

        if sp.issparse(A):
            C = sp.bmat([[A, B], [B.T, None]], format='csc')
            self._solver = _SparseSolver(C)
        else:
            Z = np.zeros((p, p), dtype=float)
            C = np.block([[A, B], [B.T, Z]])
            self._solver = _DenseSolver(C)

        if build_inverse:
            I = np.eye(n + p)
            self._inverse = self._solver.solve(I)
        else:
            self._inverse = None

        self.n = n
        self.p = p

    def solve(self, a, b=None):
        '''
        Solves for `x` and `y` given `a` and `b`.

        Parameters
        ----------
        a : (n, ...) array or sparse matrix

        b : (p, ...) array or sparse matrix, optional
            If not given, it is assumed to be zeros

        Returns
        -------
        (n, ...) array

        (p, ...) array

        '''
        a = as_array(a, dtype=float)
        if self._inverse is not None:
            xy = self._inverse[:, :self.n].dot(a)
            if b is not None:
                b = as_array(b, dtype=float)
                xy += self._inverse[:, self.n:].dot(b)
        else:
            if b is None:
                b = np.zeros((self.p,) + a.shape[1:], dtype=float)
            else:
                b = as_array(b, dtype=float)

            c = np.concatenate((a, b), axis=0)
            xy = self._solver.solve(c)

        x, y = xy[:self.n], xy[self.n:]
        return x, y


class PartitionedPosDefSolver:
    '''
    Solves the system of equations

    .. math::
        \\left[
        \\begin{array}{cc}
            A   & B \\\\
            B^T & 0 \\\\
        \\end{array}
        \\right]
        \\left[
        \\begin{array}{c}
            x \\\\
            y \\\\
        \\end{array}
        \\right]
        =
        \\left[
        \\begin{array}{c}
            a \\\\
            b \\\\
        \\end{array}
        \\right]

    for `x` and `y`, where `A` is a positive definite matrix. Rather than
    naively building and solving the system, this class partitions the inverse
    as

    .. math::
        \\left[
        \\begin{array}{cc}
            C   & D \\\\
            D^T & E \\\\
        \\end{array}
        \\right]

    where

    .. math::
        C = A^{-1} - (A^{-1} B) (B^T A^{-1} B)^{-1} (A^{-1} B)^T

    .. math::
        D = (A^{-1} B) (B^T A^{-1} B)^{-1}

    .. math::
        E = - (B^T A^{-1} B)^{-1}

    The inverse of `A` is not computed, but instead its action is performed by
    solving the Cholesky decomposition of `A`. `A` can be a scipy sparse matrix
    or a numpy array. `B` can also be either a scipy sparse matrix or a numpy
    array but it will be converted to a numpy array.

    Parameters
    ----------
    A : (n, n) array or sparse matrix

    B : (n, p) array or sparse matrix

    Note
    ----
    This class stores the factorization of `A`, which may be sparse, the dense
    matrix `A^-1 B`, and the dense factorization of `B^T A^-1 B`. If the number
    of columns in `B` is large then this may take up too much memory.

    '''
    def __init__(self, A, B, build_inverse=False):
        A = as_sparse_or_array(A, dtype=float)
        B = as_array(B, dtype=float)
        n, p = B.shape
        if n < p:
            raise np.linalg.LinAlgError(
              'There are fewer rows than columns in `B`. This makes the block '
              'matrix singular, and its inverse cannot be computed.'
              )

        if sp.issparse(A):
            if not HAS_CHOLMOD:
                warnings.warn(CHOLMOD_MSG)
                self._A_solver = _DensePosDefSolver(A.toarray())
            else:
                self._A_solver = _SparsePosDefSolver(A)

        else:
            self._A_solver = _DensePosDefSolver(A)

        self._AiB = self._A_solver.solve(B)
        self._BtAiB_solver = _DensePosDefSolver(B.T.dot(self._AiB))

        if build_inverse:
            Ia = np.eye(n)
            Ib = np.eye(p)
            E = -self._BtAiB_solver.solve(Ib)
            D = self._AiB.dot(-E)
            C = self._A_solver.solve(Ia) - D.dot(self._AiB.T)
            self._inverse = np.block([[C, D], [D.T, E]])
        else:
            self._inverse = None

        self.n = n
        self.p = p

    def solve(self, a, b=None):
        '''
        Solves for `x` and `y` given `a` and `b`.

        Parameters
        ----------
        a : (n, ...) array or sparse matrix

        b : (p, ...) array or sparse matrix

        Returns
        -------
        (n, ...) array

        (p, ...) array

        '''
        a = as_array(a, dtype=float)
        if self._inverse is not None:
            xy = self._inverse[:, :self.n].dot(a)
            if b is not None:
                b = as_array(b, dtype=float)
                xy += self._inverse[:, self.n:].dot(b)

            x, y = xy[:self.n], xy[self.n:]

        else:
            Dta = self._BtAiB_solver.solve(self._AiB.T.dot(a))
            Ca = self._A_solver.solve(a) - self._AiB.dot(Dta)
            if b is None:
                x = Ca
                y = Dta
            else:
                b = as_array(b, dtype=float)
                Eb = -self._BtAiB_solver.solve(b)
                Db = -self._AiB.dot(Eb)
                x = Ca + Db
                y = Dta + Eb

        return x, y


class GMRESSolver:
    '''
    Solves the system of equations `Ax = b` for `x` iteratively with GMRES and
    an incomplete LU decomposition.

    Parameters
    ----------
    A : (n, n) CSC sparse matrix

    drop_tol : float, optional
        Passed to `scipy.sparse.linalg.spilu`. This controls the sparsity of
        the ILU decomposition used for the preconditioner. It should be between
        0 and 1. smaller values make the decomposition denser but better
        approximates the LU decomposition. If the value is too large then you
        may get a "Factor is exactly singular" error.

    fill_factor : float, optional
        Passed to `scipy.sparse.linalg.spilu`. I believe this controls the
        memory allocated for the ILU decomposition. If this value is too small
        then memory will be allocated dynamically for the decomposition. If
        this is too large then you may get a memory error.

    normalize_inplace : bool
        If True and `A` is a csc matrix, then `A` is normalized in place.

    '''
    def __init__(self,
                 A,
                 drop_tol=0.005,
                 fill_factor=2.0,
                 normalize_inplace=False):
        # the spilu and gmres functions are most efficient with csc sparse. If
        # the matrix is already csc then this will do nothing
        A = sp.csc_matrix(A)
        n = row_norms(A)
        if normalize_inplace:
            divide_rows(A, n, inplace=True)
        else:
            A = divide_rows(A, n, inplace=False).tocsc()

        LOGGER.debug(
            'computing the ILU decomposition of a %s by %s sparse matrix with '
            '%s nonzeros ' % (A.shape + (A.nnz,))
            )
        ilu = spla.spilu(
            A,
            drop_rule='basic',
            drop_tol=drop_tol,
            fill_factor=fill_factor
            )
        LOGGER.debug('done')
        M = spla.LinearOperator(A.shape, ilu.solve)
        self.A = A
        self.M = M
        self.n = n

    def solve(self, b, tol=1.0e-10):
        '''
        Solve `Ax = b` for `x`

        Parameters
        ----------
        b : (n,) array

        tol : float, optional

        Returns
        -------
        (n,) array

        '''
        # solve the system using GMRES and define the callback function to
        # print info for each iteration
        def callback(res, _itr=[0]):
            l2 = np.linalg.norm(res)
            LOGGER.debug('GMRES error on iteration %s: %s' % (_itr[0], l2))
            _itr[0] += 1

        LOGGER.debug('solving the system with GMRES')
        x, info = spla.gmres(
            self.A,
            b/self.n,
            tol=tol,
            M=self.M,
            callback=callback
            )
        LOGGER.debug('finished GMRES with info %s' % info)
        return x
